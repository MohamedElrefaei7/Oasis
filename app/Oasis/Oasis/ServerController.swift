//
//  ServerController.swift
//  Oasis
//
//  Owns the `oasis serve` child process: spawn → handshake → health poll →
//  teardown. This is the whole app/server seam; ground truth is
//  docs/APP_SEAM.md, and every non-obvious decision below cites the section it
//  comes from.
//

import Foundation
import Observation
import OSLog

@MainActor
@Observable
final class ServerController {

    // MARK: - State

    /// The three states the UI renders, plus the two transient ones on the way in.
    enum State {
        /// Nothing spawned yet.
        case idle
        /// Process launched, waiting for the handshake line on stdout.
        case starting
        /// Handshake read, `/api/health` says `loading`. `since` drives the
        /// elapsed-seconds readout, which makes every run a re-measurement of
        /// the load window in APP_SEAM.md §4 (35–54 s measured).
        case warming(since: Date)
        /// `/api/health` returned `status: ready`.
        case ready(HealthResponse)
        /// Terminal until Retry: no binary, child died, bad handshake, or
        /// `status: error`.
        case failed(String)
    }

    private(set) var state: State = .idle

    /// Kept from the handshake: `/api/search` and every other protected route
    /// need `Authorization: Bearer <token>`. `/api/health` deliberately does
    /// not (APP_SEAM.md §3), which is why nothing used this until step 2.
    private(set) var handshake: Handshake?

    /// The last health payload, available whenever the server is ready.
    ///
    /// Lets callers read real index state (`documents`, `reindex_recommended`)
    /// without a second fetch — the readiness poll already has it.
    var health: HealthResponse? {
        if case .ready(let health) = state { return health }
        return nil
    }

    // MARK: - Tunables (all justified by APP_SEAM.md §4's measurements)

    /// Measured spawn → handshake is 2.0–3.3 s. 15 s is wide margin; past it,
    /// the line is not coming (§6a) and hanging is the one thing we must not do.
    private static let handshakeTimeout: Duration = .seconds(15)

    /// Health is a cheap in-process read; 750 ms keeps the elapsed readout live
    /// without hammering the server through its model load.
    private static let healthPollInterval: Duration = .milliseconds(750)

    /// Soft ceiling on the warming window, *not* a readiness budget. Measured
    /// handshake → ready is 35–54 s and highly variable, so this is set far
    /// above it: expiry means "something is genuinely wrong", and it lands in
    /// `.failed` with a Retry rather than a crash (APP_SEAM.md §3, §4).
    private static let readySoftTimeout: TimeInterval = 120

    /// The environment variable that names the server binary in dev runs.
    private static let binaryEnvVar = "OASIS_SERVE_BIN"

    private static let log = Logger(subsystem: "com.oasis.app", category: "server")

    // MARK: - Private state

    private var process: Process?
    private var lifecycle: Task<Void, Never>?
    private var stdoutDrain: Task<Void, Never>?
    private var stderrDrain: Task<Void, Never>?

    private enum HandshakeOutcome {
        case got(Handshake)
        case failed(String)
    }

    private enum BinaryResolution {
        case found(URL)
        case missing(String)
    }
    private var handshakeGate: CheckedContinuation<HandshakeOutcome, Never>?

    /// Bumped by every `start()`/`stop()`. A run that has been superseded
    /// (Retry, quit) checks its generation before touching `state`, so a
    /// cancelled run can never clobber the state of the run that replaced it.
    private var generation = 0

    // MARK: - Lifecycle entry points

    func start() {
        stop()
        generation += 1
        let gen = generation
        state = .starting
        lifecycle = Task { @MainActor [weak self] in
            await self?.run(generation: gen)
        }
    }

    /// Tear everything down: invalidate the in-flight run, kill the child.
    func stop() {
        generation += 1

        // Resume any run parked on the handshake continuation so its task can
        // unwind instead of leaking. The generation bump above means it will
        // return without touching `state`.
        settleHandshake(.failed("stopped"))

        lifecycle?.cancel()
        lifecycle = nil
        stdoutDrain?.cancel()
        stdoutDrain = nil
        stderrDrain?.cancel()
        stderrDrain = nil

        terminateChild()
        handshake = nil
    }

    /// Called from `AppDelegate.applicationWillTerminate` (⌘Q).
    func shutdown() {
        Self.log.notice("app terminating — tearing down the server child")
        stop()
        state = .idle
    }

    func retry() {
        Self.log.notice("retry requested")
        start()
    }

    // MARK: - The run

    private func run(generation gen: Int) async {
        // 1. Resolve the binary. Absolute path only — never $PATH.
        let binary: URL
        switch Self.resolveServerBinary() {
        case .found(let url):
            binary = url
        case .missing(let message):
            fail(message, generation: gen)
            return
        }

        // 2. Spawn.
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        let child = Process()
        child.executableURL = binary
        // `--managed` arms the server's parent-death watchdog (APP_SEAM.md §5).
        // It is load-bearing for teardown — see `terminateChild()`.
        child.arguments = ["serve", "--managed"]
        child.standardOutput = stdoutPipe
        child.standardError = stderrPipe
        child.terminationHandler = { [weak self] proc in
            // Fires on an arbitrary thread; bind once, then hop to the actor.
            let controller = self
            Task { @MainActor in controller?.handleTermination(of: proc) }
        }

        do {
            try child.run()
        } catch {
            fail("couldn't spawn \(binary.path): \(error.localizedDescription)", generation: gen)
            return
        }
        process = child
        Self.log.notice("spawned \(binary.path, privacy: .public) serve --managed (pid \(child.processIdentifier))")

        // 3. Drain BOTH pipes, continuously.
        //
        // stderr carries real volume during model load — uvicorn lifecycle
        // logs, the HF-hub warning, two `Loading weights: 100%` progress bars,
        // and any traceback (APP_SEAM.md §2). An undrained pipe fills its OS
        // buffer and *blocks the child*, which is indistinguishable from a
        // startup hang. It is logged, never parsed as a handshake.
        drainStderr(stderrPipe)

        let spawnedAt = Date()
        let outcome = await withCheckedContinuation { (continuation: CheckedContinuation<HandshakeOutcome, Never>) in
            handshakeGate = continuation
            readHandshake(from: stdoutPipe)
            startHandshakeTimeout()
        }

        guard gen == generation else { return }

        let hs: Handshake
        switch outcome {
        case .got(let value):
            hs = value
        case .failed(let message):
            fail(message, generation: gen)
            return
        }

        handshake = hs
        // Logged as a live re-measurement of APP_SEAM.md §4's t_handshake
        // (measured 2.0–3.3 s). `.public` because a duration and a loopback
        // port are not sensitive — os_log redacts interpolated strings by
        // default, which would otherwise hide the number this exists to show.
        let elapsed = Date().timeIntervalSince(spawnedAt)
        Self.log.notice(
            "handshake in \(String(format: "%.2f", elapsed), privacy: .public)s — port \(hs.port), server pid \(hs.pid)"
        )

        // 4. Poll health through the load window.
        let warmingSince = Date()
        state = .warming(since: warmingSince)
        await pollHealth(port: hs.port, since: warmingSince, generation: gen)
    }

    // MARK: - Binary resolution

    private static func resolveServerBinary() -> BinaryResolution {
        // RELEASE TODO — the shipped app spawns the PyInstaller `oasis` binary
        // bundled inside the .app (APP_SEAM.md §1), resolved off `Bundle.main`
        // rather than the environment. Deliberately not implemented yet; this
        // step only needs the dev path.
        //
        //     if let bundled = Bundle.main.url(forAuxiliaryExecutable: "oasis") { return .found(bundled) }

        guard let path = ProcessInfo.processInfo.environment[binaryEnvVar], !path.isEmpty else {
            return .missing("""
                \(binaryEnvVar) is not set.

                Point it at the `oasis` binary to run (Product ▸ Scheme ▸ Edit Scheme… \
                ▸ Run ▸ Arguments ▸ Environment Variables), e.g. \
                /path/to/oasis/.pixi/envs/default/bin/oasis — see app/README-dev.md.

                It must be the pixi env's binary. A `.venv/bin/oasis` from before \
                the pixi migration runs Homebrew Python against a PyPI torch, whose \
                Accelerate BLAS SIGBUSes during model warmup.
                """)
        }

        // No $PATH fallback, on purpose: this machine carries a stale `oasis` on
        // PATH that predates `serve`, so a fallback would silently launch the
        // wrong binary and fail in a much more confusing way than this message.
        let url = URL(fileURLWithPath: path)
        guard FileManager.default.isExecutableFile(atPath: url.path) else {
            return .missing("\(binaryEnvVar) points at \(url.path), which doesn't exist or isn't executable.")
        }
        return .found(url)
    }

    // MARK: - Handshake

    private func readHandshake(from pipe: Pipe) {
        stdoutDrain = Task { @MainActor [weak self] in
            var sawFirstLine = false
            do {
                for try await line in pipe.fileHandleForReading.bytes.lines {
                    if Task.isCancelled { return }

                    guard !sawFirstLine else {
                        // APP_SEAM.md §2: stdout is exactly one line. Anything
                        // here is a deviation from the doc worth seeing in the
                        // console — but it is never re-parsed as a handshake.
                        Self.log.warning("unexpected stdout after the handshake: \(line, privacy: .public)")
                        continue
                    }
                    sawFirstLine = true

                    let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard
                        let data = trimmed.data(using: .utf8),
                        let decoded = try? JSONDecoder().decode(Handshake.self, from: data)
                    else {
                        // §2 is explicit: don't keep scanning hoping a handshake
                        // shows up later. It never will.
                        self?.settleHandshake(.failed("first stdout line was not handshake JSON: \(trimmed)"))
                        return
                    }
                    self?.settleHandshake(.got(decoded))
                    // Keep looping: the pipe still has to be drained.
                }
                if !sawFirstLine {
                    self?.settleHandshake(.failed("server closed stdout without writing a handshake"))
                }
            } catch {
                self?.settleHandshake(.failed("couldn't read the server's stdout: \(error.localizedDescription)"))
            }
        }
    }

    private func startHandshakeTimeout() {
        let gen = generation
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: Self.handshakeTimeout)
            guard let self, gen == self.generation, !Task.isCancelled else { return }
            self.settleHandshake(.failed("no handshake within \(Self.handshakeTimeout) of spawning the server"))
        }
    }

    /// Idempotent: only the first outcome wins. Everything that can settle the
    /// handshake — the stdout reader, the timeout, `terminationHandler`, and
    /// `stop()` — races, and all of them are serialized on the main actor.
    private func settleHandshake(_ outcome: HandshakeOutcome) {
        guard let continuation = handshakeGate else { return }
        handshakeGate = nil
        continuation.resume(returning: outcome)
    }

    // MARK: - Health polling

    private func pollHealth(port: Int, since: Date, generation gen: Int) async {
        let url = URL(string: "http://127.0.0.1:\(port)/api/health")!
        let configuration = URLSessionConfiguration.ephemeral
        // Health answers instantly once the socket is up (uvicorn accepts
        // connections long before the models finish); a short per-request
        // timeout keeps polls from stacking. The 60 s default is the one
        // APP_SEAM.md §3 warns about, and it applies to *requests*, never to
        // how long we're willing to stay in `.warming`.
        configuration.timeoutIntervalForRequest = 10
        configuration.waitsForConnectivity = false
        let session = URLSession(configuration: configuration)
        let decoder = JSONDecoder()

        let deadline = Date().addingTimeInterval(Self.readySoftTimeout)
        var poll = 0

        while !Task.isCancelled {
            guard gen == generation else { return }
            poll += 1

            do {
                var request = URLRequest(url: url)
                request.cachePolicy = .reloadIgnoringLocalCacheData
                let (data, _) = try await session.data(for: request)
                guard gen == generation else { return }
                let health = try decoder.decode(HealthResponse.self, from: data)

                switch health.status {
                case .loading:
                    // Never a failure (APP_SEAM.md §6c). The measured window is
                    // 35–54 s and we sit here for all of it, by design.
                    if poll % 8 == 0 {
                        Self.log.debug("health: loading (poll \(poll))")
                    }
                case .ready:
                    // The other half of the §4 re-measurement: t_ready, the
                    // warming window the user just sat through.
                    let warmed = Date().timeIntervalSince(since)
                    Self.log.notice(
                        "health: ready after \(String(format: "%.2f", warmed), privacy: .public)s warming — documents=\(health.documents.map(String.init) ?? "null", privacy: .public) reindex_recommended=\(health.reindexRecommended ?? false)"
                    )
                    state = .ready(health)
                    return
                case .error:
                    // §6b: show the real error, not a spinner forever.
                    fail(health.error ?? "the server reported an error with no message", generation: gen)
                    return
                }
            } catch {
                // Connection-refused right after the handshake is expected and
                // transient — the socket is bound but the request can still
                // lose a race, and a single miss says nothing. Retry; only the
                // soft deadline below ends the loop.
                Self.log.debug("health poll \(poll) failed (transient): \(error.localizedDescription, privacy: .public)")
            }

            if Date() >= deadline {
                fail(
                    "the server never reported ready within \(Int(Self.readySoftTimeout))s. "
                        + "It may still be loading models — check the console for the child's stderr.",
                    generation: gen
                )
                return
            }

            try? await Task.sleep(for: Self.healthPollInterval)
        }
    }

    // MARK: - Termination and teardown

    private func handleTermination(of proc: Process) {
        // Ignore handlers from a superseded run; `terminateChild()` clears the
        // handler on the process it kills, so reaching here means the child
        // exited on its own.
        guard proc === process else { return }

        let status = proc.terminationStatus
        Self.log.error("server child exited (status \(status), reason \(proc.terminationReason.rawValue))")

        // Before the handshake, this is APP_SEAM.md §6a — no-op if a handshake
        // already arrived.
        settleHandshake(.failed("server exited before the handshake (status \(status))"))

        // After it, this is §6d: the child died out from under a running app.
        switch state {
        case .warming, .ready:
            process = nil
            state = .failed("the server process exited unexpectedly (status \(status)).")
        case .idle, .starting, .failed:
            break
        }
    }

    /// Terminate the child we own, if any.
    ///
    /// **Why there are two teardown mechanisms and why both are needed.**
    /// This one — SIGTERM from `applicationWillTerminate`, which uvicorn
    /// shuts down gracefully on — covers a clean quit (⌘Q). It does *not*
    /// cover Stop in Xcode (⌘.): Xcode SIGKILLs the app, so
    /// `applicationWillTerminate` never runs and nothing here executes. That
    /// case is covered by the other mechanism, the `--managed` watchdog we
    /// spawn with (APP_SEAM.md §5): the child reparents to launchd, its
    /// `getppid()` poll returns 1, and it exits within ~1 s.
    ///
    /// Neither covers the other's case, which is why both exist. Between them,
    /// there should never be an orphaned `oasis serve` in Activity Monitor —
    /// that is the specific failure this design prevents, and the thing to
    /// verify after every run.
    private func terminateChild() {
        guard let proc = process else { return }
        process = nil
        proc.terminationHandler = nil  // this exit is ours, not a crash
        guard proc.isRunning else { return }

        Self.log.notice("SIGTERM → server child (pid \(proc.processIdentifier))")
        proc.terminate()

        // Bounded wait so a clean quit really is clean — long enough for
        // uvicorn's graceful shutdown, short enough not to stall quit. If it
        // outlives this, the watchdog takes it.
        let deadline = Date().addingTimeInterval(2)
        while proc.isRunning && Date() < deadline {
            usleep(20_000)
        }
        if proc.isRunning {
            Self.log.warning("child still alive after SIGTERM + 2s — leaving it to the --managed watchdog")
        }
    }

    // MARK: - Failure

    private func fail(_ message: String, generation gen: Int) {
        guard gen == generation else { return }
        Self.log.error("failed: \(message, privacy: .public)")
        terminateChild()
        state = .failed(message)
    }

    // MARK: - stderr

    private func drainStderr(_ pipe: Pipe) {
        stderrDrain = Task { @MainActor in
            do {
                for try await line in pipe.fileHandleForReading.bytes.lines {
                    if Task.isCancelled { return }
                    Self.log.debug("server: \(line, privacy: .public)")
                }
            } catch {
                Self.log.warning("stderr drain ended: \(error.localizedDescription, privacy: .public)")
            }
        }
    }
}
