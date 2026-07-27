//
//  IndexViewModel.swift
//  Oasis
//
//  The index flow: folder picker → POST /api/index → the SSE progress stream →
//  a terminal summary, with cooperative cancel. The first thing in the app that
//  writes to the index, and the first consumer of the async-index machinery
//  built server-side (api/index.py, api/jobs.py).
//
//  Reads `port`/`token` off the handshake `ServerController` holds — both the
//  POST and the SSE GET are token-gated (header auth, which `URLSession` can do
//  and a browser `EventSource` cannot; that's why the server took a header
//  rather than a query-param token).
//

import AppKit
import Foundation
import Observation
import OSLog

@MainActor
@Observable
final class IndexViewModel {

    // MARK: - State

    /// The index state machine. Every terminal case is reached by an *event off
    /// the stream*, never by a local action — see `cancel()`.
    enum State {
        case idle
        /// POSTed, waiting on the 202.
        case starting
        /// Running. `phase` is the discriminator the progress bar keys off:
        /// `scan` (total unknown → indeterminate) vs `embed` (total known →
        /// determinate) vs `reconciling` (the stale sweep, usually a blink).
        case indexing(phase: String?, stats: IndexStats, done: Int, total: Int?)
        case done(IndexStats)
        /// Partial stats — committed work stays committed.
        case cancelled(IndexStats)
        case failed(String)

        var isRunning: Bool {
            switch self {
            case .starting, .indexing: true
            case .idle, .done, .cancelled, .failed: false
            }
        }

        var isTerminal: Bool {
            switch self {
            case .done, .cancelled, .failed: true
            case .idle, .starting, .indexing: false
            }
        }
    }

    private(set) var state: State = .idle

    /// Cancel is *requested*, not synchronously effected: the pipeline finishes
    /// its current file/batch and emits a terminal `cancelled` event. This flag
    /// is what puts the sheet in "Cancelling…" for that window.
    private(set) var isCancelling = false

    /// The id from the 202. Required by `POST /api/index/cancel`, and used to
    /// ignore events belonging to some other job.
    private(set) var jobID: String?

    /// The folder being indexed, for the sheet's subtitle.
    private(set) var root: URL?

    /// Sheet presentation. Settable so `ContentView` can bind to it.
    var isPresenting = false

    /// Called after a successful index, once health has been re-fetched, so the
    /// search UI can re-derive its resting state (the empty-index onboarding
    /// prompt has to clear).
    var onIndexCompleted: (() -> Void)?

    // MARK: - Private

    private static let log = Logger(subsystem: "com.oasis.app", category: "index")

    private let controller: ServerController
    private var streamTask: Task<Void, Never>?

    /// Short-lived calls: POST /api/index, POST /api/index/cancel.
    private let session: URLSession
    /// The SSE stream. Its request timeout is an *inactivity* timeout, so it
    /// only has to exceed the server's 15 s heartbeat — the `: ping` comments
    /// are what keep a quiet-but-live stream from being reaped. A minute is
    /// four missed heartbeats: dead, not idle.
    private let streamSession: URLSession

    init(controller: ServerController) {
        self.controller = controller

        let short = URLSessionConfiguration.ephemeral
        short.timeoutIntervalForRequest = 15
        short.waitsForConnectivity = false
        self.session = URLSession(configuration: short)

        let streaming = URLSessionConfiguration.ephemeral
        streaming.timeoutIntervalForRequest = 60
        // A first-time index of ~/Documents is minutes; the resource timeout must
        // not be the thing that ends it. (Default is 7 days — set explicitly so a
        // future config change can't silently cap a long index.)
        streaming.timeoutIntervalForResource = 24 * 60 * 60
        streaming.waitsForConnectivity = false
        self.streamSession = URLSession(configuration: streaming)
    }

    // MARK: - Entry point

    /// Folder picker → index. The chosen URL's path is the `root`.
    func chooseFolderAndIndex() {
        guard !state.isRunning else {
            Self.log.debug("index already running — ignoring a second request")
            isPresenting = true
            return
        }

        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.title = "Index a Folder"
        panel.message = "Choose a folder for Oasis to index."
        panel.prompt = "Index"
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser

        guard panel.runModal() == .OK, let folder = panel.urls.first else {
            Self.log.debug("folder picker cancelled")
            return
        }
        start(root: folder)
    }

    func start(root folder: URL) {
        guard !state.isRunning else { return }

        root = folder
        jobID = nil
        isCancelling = false
        state = .starting
        isPresenting = true

        streamTask?.cancel()
        streamTask = Task { @MainActor [weak self] in
            await self?.runIndex(root: folder)
        }
    }

    /// Close the sheet after a terminal state. Resets to `.idle` so the next
    /// index starts clean.
    func dismiss() {
        isPresenting = false
        guard state.isTerminal else { return }
        state = .idle
        isCancelling = false
        jobID = nil
        root = nil
    }

    // MARK: - Kickoff

    private func runIndex(root folder: URL) async {
        guard let handshake = controller.handshake else {
            state = .failed("The server isn't running.")
            return
        }

        guard let url = Self.endpoint(port: handshake.port, path: "/api/index") else {
            state = .failed("Couldn't build the index URL.")
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            // A new folder is incremental — `force` is Reindex's job, and it
            // governs re-embedding, not walking.
            request.httpBody = try JSONEncoder().encode(IndexRequest(root: folder.path, force: false))
        } catch {
            state = .failed("Couldn't encode the index request: \(error.localizedDescription)")
            return
        }

        Self.log.notice("POST /api/index root=\(folder.path, privacy: .public) force=false")

        let data: Data
        let status: Int
        do {
            let (body, response) = try await session.data(for: request)
            data = body
            status = (response as? HTTPURLResponse)?.statusCode ?? 0
        } catch {
            guard !Task.isCancelled else { return }
            state = .failed("Couldn't reach the server: \(error.localizedDescription)")
            return
        }
        guard !Task.isCancelled else { return }

        switch status {
        case 202:
            guard let job = try? JSONDecoder().decode(JobResponse.self, from: data) else {
                state = .failed("The server accepted the index job but sent an unreadable reply.")
                return
            }
            jobID = job.jobID
            state = .indexing(phase: nil, stats: .empty, done: 0, total: nil)
            Self.log.notice("index job \(job.jobID, privacy: .public) started — connecting to the event stream")
            await consumeEvents(port: handshake.port, token: handshake.token)

        case 409:
            // Single-job lock: one is already running. The local `isRunning`
            // guard stops *this* UI from double-starting; this is the case where
            // something else did (a stray CLI run, a stale job).
            let message = Self.errorMessage(from: data) ?? "An index job is already running."
            Self.log.error("index rejected (409): \(message, privacy: .public)")
            state = .failed(message)

        case 400:
            // Shouldn't happen from a picker — the panel only returns
            // directories — but the endpoint validates and so does this.
            let message = Self.errorMessage(from: data) ?? "That isn't a directory."
            Self.log.error("index rejected (400): \(message, privacy: .public)")
            state = .failed(message)

        default:
            let message = Self.errorMessage(from: data) ?? "the server returned HTTP \(status)."
            Self.log.error("index failed (\(status)): \(message, privacy: .public)")
            state = .failed("Indexing couldn't start — \(message)")
        }
    }

    // MARK: - The event stream

    /// Consume `GET /api/index/events` until a terminal event settles the state.
    ///
    /// One reconnect is allowed. The stream can end without a terminal event —
    /// the app was backgrounded, the connection dropped — and the snapshot-first
    /// contract makes recovery exact rather than a guess: a re-connect's first
    /// event is the job's current (possibly already-terminal) state.
    private func consumeEvents(port: Int, token: String) async {
        for attempt in 1...2 {
            let settled = await streamOnce(port: port, token: token, attempt: attempt)
            if settled || Task.isCancelled { return }
            if state.isTerminal { return }
            Self.log.warning("event stream ended without a terminal event (attempt \(attempt)) — re-attaching")
            try? await Task.sleep(for: .milliseconds(400))
            if Task.isCancelled { return }
        }
        guard !state.isTerminal else { return }
        state = .failed(
            "Lost the progress stream. The index may still be running — reopen Oasis to re-attach."
        )
    }

    /// One connection. Returns `true` when a terminal event settled the state.
    private func streamOnce(port: Int, token: String, attempt: Int) async -> Bool {
        guard let url = Self.endpoint(port: port, path: "/api/index/events") else {
            state = .failed("Couldn't build the event-stream URL.")
            return true
        }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.cachePolicy = .reloadIgnoringLocalCacheData

        do {
            let (bytes, response) = try await streamSession.bytes(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard status == 200 else {
                // Drain enough to read the envelope; the body is one small JSON.
                var body = Data()
                for try await byte in bytes.prefix(4096) { body.append(byte) }
                let message = Self.errorMessage(from: body) ?? "the server returned HTTP \(status)."
                state = .failed("Couldn't follow indexing progress — \(message)")
                return true
            }

            var parser = SSEFrameParser()
            let decoder = JSONDecoder()

            // Byte-wise, not `bytes.lines`: Foundation's line sequence drops
            // empty lines, and the empty line is what terminates an SSE event.
            // See `SSEFrameParser` — this is the bug the first run of this flow
            // hit, and it presents as a stream that simply never delivers.
            for try await byte in bytes {
                if Task.isCancelled { return true }
                // Buffer until a blank line closes the message; `: ping`
                // heartbeats and `event:` lines never make it past here.
                guard let payload = parser.consume(byte) else { continue }
                guard let data = payload.data(using: .utf8) else { continue }

                let event: IndexEvent
                do {
                    event = try decoder.decode(IndexEvent.self, from: data)
                } catch {
                    // A malformed frame is not worth ending a live index over —
                    // progress carries absolute counts, so the next tick heals it.
                    Self.log.error("undecodable index event: \(error.localizedDescription, privacy: .public)")
                    continue
                }

                if apply(event) { return true }
            }
        } catch is CancellationError {
            return true
        } catch let error as URLError where error.code == .cancelled {
            return true
        } catch {
            guard !Task.isCancelled else { return true }
            Self.log.error("event stream error (attempt \(attempt)): \(error.localizedDescription, privacy: .public)")
            return false  // let the caller re-attach
        }

        return false  // stream closed cleanly without a terminal event
    }

    /// Fold one event into the state machine. Returns `true` when it was terminal.
    private func apply(_ event: IndexEvent) -> Bool {
        // Never let another job's events drive this sheet. Same reasoning as
        // cancel-by-job_id: "whatever is running" is not what we're watching.
        if let ours = jobID, let theirs = event.jobID, ours != theirs {
            Self.log.warning("ignoring an event for job \(theirs, privacy: .public) (watching \(ours, privacy: .public))")
            return false
        }

        switch event {
        case .snapshot(let snapshot):
            // Decoded exactly like a progress update — the first event on
            // connect is a snapshot, and on a re-attach it may already be
            // terminal.
            switch snapshot.status {
            case "running":
                state = .indexing(
                    phase: snapshot.phase,
                    stats: snapshot.stats,
                    done: snapshot.done,
                    total: snapshot.total
                )
                return false
            case "done":
                settleDone(snapshot.stats)
                return true
            case "cancelled":
                settleCancelled(snapshot.stats)
                return true
            case "error":
                state = .failed(snapshot.error ?? "Indexing failed.")
                return true
            default:
                // "idle" — no job has ever run. Impossible right after our own
                // 202, so treat it as a lost stream and let the re-attach path
                // decide, rather than claiming a completion that never happened.
                Self.log.warning("snapshot reported status \(snapshot.status, privacy: .public)")
                return false
            }

        case .progress(let progress):
            state = .indexing(
                phase: progress.phase,
                stats: progress.stats,
                done: progress.done,
                total: progress.total
            )
            return false

        case .done(let terminal):
            settleDone(terminal.stats)
            return true

        case .cancelled(let terminal):
            settleCancelled(terminal.stats)
            return true

        case .failed(let failure):
            Self.log.error("index job failed: \(failure.message, privacy: .public)")
            state = .failed(failure.message)
            return true

        case .unknown(let type):
            Self.log.debug("ignoring unknown event type \(type, privacy: .public)")
            return false
        }
    }

    private func settleDone(_ stats: IndexStats) {
        isCancelling = false
        state = .done(stats)
        Self.log.notice(
            "index done — indexed=\(stats.indexed) skipped=\(stats.skipped) chunks=\(stats.chunks) removed=\(stats.removed) permission_denied=\(stats.permissionDenied) failed=\(stats.failed) unsupported=\(stats.unsupported)"
        )
        // The server's `documents` just changed; the app's held HealthResponse
        // is now stale. Without this the freshly-indexed folder still shows
        // "nothing indexed" and the empty state never clears.
        Task { @MainActor [weak self] in
            await self?.controller.refreshHealth()
            self?.onIndexCompleted?()
        }
    }

    private func settleCancelled(_ stats: IndexStats) {
        isCancelling = false
        state = .cancelled(stats)
        Self.log.notice(
            "index cancelled — partial: indexed=\(stats.indexed) chunks=\(stats.chunks) permission_denied=\(stats.permissionDenied)"
        )
        // Committed work is real work: a cancelled run still changed the count.
        Task { @MainActor [weak self] in
            await self?.controller.refreshHealth()
            self?.onIndexCompleted?()
        }
    }

    // MARK: - Cancel

    /// Request cancellation. **Does not tear anything down.**
    ///
    /// Cancel is cooperative: the pipeline checks the flag between files and
    /// between embed batches, finishes what it's on, and emits a terminal
    /// `cancelled` event with partial stats. So this only flips the sheet to
    /// "Cancelling…" and keeps consuming — the UI settles on the *event*, not on
    /// the click. Dropping the stream here would throw away the very stats the
    /// cancel is about to produce.
    func cancel() {
        guard state.isRunning, !isCancelling else { return }
        guard let jobID, let handshake = controller.handshake else { return }

        isCancelling = true
        Task { @MainActor [weak self] in
            await self?.requestCancel(jobID: jobID, handshake: handshake)
        }
    }

    private func requestCancel(jobID id: String, handshake: Handshake) async {
        guard let url = Self.endpoint(port: handshake.port, path: "/api/index/cancel") else { return }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode(CancelRequest(jobID: id))

        do {
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            switch status {
            case 202:
                // Requested. Keep consuming; the terminal event settles the UI.
                Self.log.notice("cancel accepted for job \(id, privacy: .public) — waiting for the terminal event")
            case 409:
                // The job already ended — a terminal event is already in flight
                // or delivered. Nothing to do but let the stream settle.
                Self.log.notice("cancel 409 for job \(id, privacy: .public) — the job already finished")
            default:
                let message = Self.errorMessage(from: data) ?? "the server returned HTTP \(status)."
                Self.log.error("cancel failed (\(status)): \(message, privacy: .public)")
                isCancelling = false
            }
        } catch {
            Self.log.error("cancel request failed: \(error.localizedDescription, privacy: .public)")
            isCancelling = false
        }
    }

    // MARK: - Helpers

    private static func endpoint(port: Int, path: String) -> URL? {
        var components = URLComponents()
        components.scheme = "http"
        components.host = "127.0.0.1"
        components.port = port
        components.path = path
        return components.url
    }

    /// Pull `message` out of the `{error: {code, message}}` envelope every
    /// endpoint uses.
    private static func errorMessage(from data: Data) -> String? {
        try? JSONDecoder().decode(ErrorResponse.self, from: data).error.message
    }
}
