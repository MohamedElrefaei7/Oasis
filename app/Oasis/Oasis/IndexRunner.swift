//
//  IndexRunner.swift
//  Oasis
//
//  One index job, end to end: POST /api/index → consume the SSE stream →
//  terminal outcome. Extracted from step 3's `IndexViewModel` so that Index New
//  Folder (one root) and Reindex Current Folders (N roots, sequentially) share
//  a single implementation.
//
//  **This is the one place the SSE machinery lives**, and it stays that way on
//  purpose: the framing, the `.lines`-drops-empty-lines workaround (see
//  `SSEFrameParser`), the snapshot-vs-progress handling and the re-attach are
//  the fiddly parts with a landmine in them. Copy-pasting them for a second
//  caller is how that landmine grows back.
//

import Foundation
import Observation
import OSLog

@MainActor
final class IndexRunner {

    /// A progress tick. Absolute counts, so the consumer just renders the
    /// latest — a dropped event self-heals on the next one.
    struct Progress {
        /// `scan` | `embed` | `reconciling`. The discriminator the progress bar
        /// keys off — never "is `total` nil".
        let phase: String?
        let stats: IndexStats
        let done: Int
        let total: Int?
    }

    /// How one job ended.
    enum Outcome {
        /// Ran to completion. Stats are final and authoritative.
        case done(IndexStats)
        /// Cancelled cooperatively. Stats are partial — committed work stays
        /// committed, so this is real progress, not a rollback.
        case cancelled(IndexStats)
        case failed(String)
        /// The *local* consumer stopped (its Task was cancelled); the server-side
        /// job is unaffected and may still be running. Distinct from `.failed`
        /// because nothing went wrong and there is nothing to report.
        case interrupted
    }

    /// Reply to a cancel request.
    enum CancelAck {
        /// `202` — requested. The job ends a beat later with a terminal event.
        case requested
        /// `409` — that job isn't running any more; its terminal event is
        /// already in flight or delivered.
        case alreadyFinished
        case failed(String)
    }

    /// The id from the `202`. Cancel is bound to it (a bodyless "cancel whatever
    /// is running" would, once auto-reindex exists, be able to kill the wrong
    /// job), and events naming a different job are ignored.
    private(set) var jobID: String?

    private static let log = Logger(subsystem: "com.oasis.app", category: "index")

    private let controller: ServerController

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

    // MARK: - Run one job

    /// Start an index job over `root` and follow it to its terminal event.
    ///
    /// `force` governs *re-embedding*, not walking: a `force: false` run still
    /// does the full walk, so the reconciliation sweep and the no-vector
    /// backfill both happen. Both callers pass `false` today — `true` is only
    /// needed if the embedding model's dimension ever changes.
    func run(
        root: String,
        force: Bool,
        onProgress: (Progress) -> Void
    ) async -> Outcome {
        jobID = nil

        guard let handshake = controller.handshake else {
            return .failed("The server isn't running.")
        }
        guard let url = Self.endpoint(port: handshake.port, path: "/api/index") else {
            return .failed("Couldn't build the index URL.")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            request.httpBody = try JSONEncoder().encode(IndexRequest(root: root, force: force))
        } catch {
            return .failed("Couldn't encode the index request: \(error.localizedDescription)")
        }

        Self.log.notice("POST /api/index root=\(root, privacy: .public) force=\(force)")

        let data: Data
        let status: Int
        do {
            let (body, response) = try await session.data(for: request)
            data = body
            status = (response as? HTTPURLResponse)?.statusCode ?? 0
        } catch {
            guard !Task.isCancelled else { return .interrupted }
            return .failed("Couldn't reach the server: \(error.localizedDescription)")
        }
        guard !Task.isCancelled else { return .interrupted }

        switch status {
        case 202:
            guard let job = try? JSONDecoder().decode(JobResponse.self, from: data) else {
                return .failed("The server accepted the index job but sent an unreadable reply.")
            }
            jobID = job.jobID
            Self.log.notice("index job \(job.jobID, privacy: .public) started — connecting to the event stream")

        case 409:
            // Single-job lock. Callers serialize their own jobs, so reaching
            // here means something else is indexing (a stray CLI run, a job
            // left over from a previous window).
            let message = Self.errorMessage(from: data) ?? "An index job is already running."
            Self.log.error("index rejected (409): \(message, privacy: .public)")
            return .failed(message)

        case 400:
            // Reindex can genuinely hit this where the picker can't: a recorded
            // root that has since been deleted or unmounted is no longer a
            // directory.
            let message = Self.errorMessage(from: data) ?? "That isn't a directory: \(root)"
            Self.log.error("index rejected (400): \(message, privacy: .public)")
            return .failed(message)

        default:
            let message = Self.errorMessage(from: data) ?? "the server returned HTTP \(status)."
            Self.log.error("index failed (\(status)): \(message, privacy: .public)")
            return .failed("Indexing couldn't start — \(message)")
        }

        return await consumeEvents(handshake: handshake, onProgress: onProgress)
    }

    // MARK: - The event stream

    /// Consume `GET /api/index/events` until a terminal event.
    ///
    /// One reconnect is allowed. The stream can end without a terminal event —
    /// the app was backgrounded, the connection dropped — and the snapshot-first
    /// contract makes recovery exact rather than a guess: a re-connect's first
    /// event is the job's current (possibly already-terminal) state.
    private func consumeEvents(
        handshake: Handshake,
        onProgress: (Progress) -> Void
    ) async -> Outcome {
        for attempt in 1...2 {
            if let outcome = await streamOnce(handshake: handshake, attempt: attempt, onProgress: onProgress) {
                return outcome
            }
            if Task.isCancelled { return .interrupted }
            Self.log.warning("event stream ended without a terminal event (attempt \(attempt)) — re-attaching")
            try? await Task.sleep(for: .milliseconds(400))
            if Task.isCancelled { return .interrupted }
        }
        return .failed(
            "Lost the progress stream. The index may still be running — reopen Oasis to re-attach."
        )
    }

    /// One connection. Returns the outcome when a terminal event arrived, `nil`
    /// when the stream ended without one (the caller re-attaches).
    private func streamOnce(
        handshake: Handshake,
        attempt: Int,
        onProgress: (Progress) -> Void
    ) async -> Outcome? {
        guard let url = Self.endpoint(port: handshake.port, path: "/api/index/events") else {
            return .failed("Couldn't build the event-stream URL.")
        }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
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
                return .failed("Couldn't follow indexing progress — \(message)")
            }

            var parser = SSEFrameParser()
            let decoder = JSONDecoder()

            // Byte-wise, not `bytes.lines`: Foundation's line sequence drops
            // empty lines, and the empty line is what terminates an SSE event.
            // See `SSEFrameParser` — this is the bug the first run of step 3
            // hit, and it presents as a stream that simply never delivers.
            for try await byte in bytes {
                if Task.isCancelled { return .interrupted }
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

                if let outcome = apply(event, onProgress: onProgress) { return outcome }
            }
        } catch is CancellationError {
            return .interrupted
        } catch let error as URLError where error.code == .cancelled {
            return .interrupted
        } catch {
            guard !Task.isCancelled else { return .interrupted }
            Self.log.error("event stream error (attempt \(attempt)): \(error.localizedDescription, privacy: .public)")
            return nil  // let the caller re-attach
        }

        return nil  // stream closed cleanly without a terminal event
    }

    /// Fold one event in. Returns an outcome when the event was terminal.
    private func apply(_ event: IndexEvent, onProgress: (Progress) -> Void) -> Outcome? {
        // Never let another job's events drive this run. Same reasoning as
        // cancel-by-job_id: "whatever is running" is not what we're watching.
        if let ours = jobID, let theirs = event.jobID, ours != theirs {
            Self.log.warning("ignoring an event for job \(theirs, privacy: .public) (watching \(ours, privacy: .public))")
            return nil
        }

        switch event {
        case .snapshot(let snapshot):
            // Decoded exactly like a progress update — the first event on
            // connect is a snapshot, and on a re-attach it may already be
            // terminal.
            switch snapshot.status {
            case "running":
                onProgress(Progress(
                    phase: snapshot.phase,
                    stats: snapshot.stats,
                    done: snapshot.done,
                    total: snapshot.total
                ))
                return nil
            case "done":
                return .done(snapshot.stats)
            case "cancelled":
                return .cancelled(snapshot.stats)
            case "error":
                return .failed(snapshot.error ?? "Indexing failed.")
            default:
                // "idle" — no job has ever run. Impossible right after our own
                // 202, so treat it as a lost stream and let the re-attach path
                // decide, rather than claiming a completion that never happened.
                Self.log.warning("snapshot reported status \(snapshot.status, privacy: .public)")
                return nil
            }

        case .progress(let progress):
            onProgress(Progress(
                phase: progress.phase,
                stats: progress.stats,
                done: progress.done,
                total: progress.total
            ))
            return nil

        case .done(let terminal):
            Self.log.notice(
                "job done — indexed=\(terminal.stats.indexed) skipped=\(terminal.stats.skipped) chunks=\(terminal.stats.chunks) removed=\(terminal.stats.removed) permission_denied=\(terminal.stats.permissionDenied) failed=\(terminal.stats.failed) unsupported=\(terminal.stats.unsupported)"
            )
            return .done(terminal.stats)

        case .cancelled(let terminal):
            Self.log.notice(
                "job cancelled — partial: indexed=\(terminal.stats.indexed) chunks=\(terminal.stats.chunks) removed=\(terminal.stats.removed)"
            )
            return .cancelled(terminal.stats)

        case .failed(let failure):
            Self.log.error("index job failed: \(failure.message, privacy: .public)")
            return .failed(failure.message)

        case .unknown(let type):
            Self.log.debug("ignoring unknown event type \(type, privacy: .public)")
            return nil
        }
    }

    // MARK: - Cancel

    /// Request cancellation of the job this runner is following.
    ///
    /// **Does not tear the stream down.** Cancel is cooperative: the pipeline
    /// checks the flag between files and between embed batches, finishes what
    /// it's on, and emits a terminal `cancelled` event with partial stats. The
    /// caller settles on that *event*, not on this call returning — dropping the
    /// stream here would throw away the very stats the cancel produces.
    func requestCancel() async -> CancelAck {
        guard let id = jobID, let handshake = controller.handshake else {
            return .failed("No job to cancel.")
        }
        guard let url = Self.endpoint(port: handshake.port, path: "/api/index/cancel") else {
            return .failed("Couldn't build the cancel URL.")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode(CancelRequest(jobID: id))

        do {
            let (data, response) = try await session.data(for: request)
            switch (response as? HTTPURLResponse)?.statusCode ?? 0 {
            case 202:
                Self.log.notice("cancel accepted for job \(id, privacy: .public) — waiting for the terminal event")
                return .requested
            case 409:
                Self.log.notice("cancel 409 for job \(id, privacy: .public) — the job already finished")
                return .alreadyFinished
            case let status:
                let message = Self.errorMessage(from: data) ?? "the server returned HTTP \(status)."
                Self.log.error("cancel failed (\(status)): \(message, privacy: .public)")
                return .failed(message)
            }
        } catch {
            Self.log.error("cancel request failed: \(error.localizedDescription, privacy: .public)")
            return .failed(error.localizedDescription)
        }
    }

    // MARK: - Helpers

    static func endpoint(port: Int, path: String) -> URL? {
        var components = URLComponents()
        components.scheme = "http"
        components.host = "127.0.0.1"
        components.port = port
        components.path = path
        return components.url
    }

    /// Pull `message` out of the `{error: {code, message}}` envelope every
    /// endpoint uses.
    static func errorMessage(from data: Data) -> String? {
        try? JSONDecoder().decode(ErrorResponse.self, from: data).error.message
    }
}
