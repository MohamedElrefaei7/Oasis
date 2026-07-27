//
//  IndexModels.swift
//  Oasis
//
//  Wire mirrors of the three index routes and the SSE event stream. Field names
//  come from src/oasis/api/schemas.py (`IndexRequest`, `JobResponse`,
//  `CancelRequest`, `SnapshotEvent`, `ProgressEvent`, `DoneEvent`,
//  `CancelledEvent`, `ErrorEvent`) — not invented.
//
//  Plus the SSE frame parser, which lives here because framing is a wire
//  concern: the stream is `text/event-stream`, not newline-delimited JSON, and
//  one line is *not* one event.
//

import Foundation

// MARK: - Requests / responses

/// Body of `POST /api/index`. The field is `root` (a directory to walk), never
/// `path` (`/api/open`'s file) — the distinct names exist to keep the two apart.
struct IndexRequest: Codable, Sendable {
    let root: String
    /// `force` governs *re-embedding*, not walking. A new folder is incremental;
    /// Reindex is what sets this.
    let force: Bool
}

/// `202` from `POST /api/index` and from `POST /api/index/cancel`.
struct JobResponse: Codable, Sendable {
    let jobID: String
    let status: String  // running | done | cancelled | error

    private enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case status
    }
}

/// Body of `POST /api/index/cancel`. **`job_id` is required**: a bodyless
/// "cancel whatever is running" would, once auto-reindex exists, be able to kill
/// job N+1 with a tap aimed at job N. The client already holds the id.
struct CancelRequest: Codable, Sendable {
    let jobID: String

    private enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
    }
}

// MARK: - Stats

/// The `stats` payload on every event.
///
/// Server-side this is a plain `dict[str, int]`, so it is decoded as one rather
/// than as a fixed struct: an added counter must not turn the progress stream
/// into a decode failure. The named accessors cover the keys the pipeline
/// actually publishes (`_ZERO_STATS` in `api/index.py`).
struct IndexStats: Codable, Sendable, Equatable {
    let values: [String: Int]

    static let empty = IndexStats(values: [:])

    init(values: [String: Int]) {
        self.values = values
    }

    init(from decoder: Decoder) throws {
        values = try decoder.singleValueContainer().decode([String: Int].self)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(values)
    }

    subscript(key: String) -> Int { values[key] ?? 0 }

    var indexed: Int { self["indexed"] }
    var skipped: Int { self["skipped"] }
    var failed: Int { self["failed"] }
    var unsupported: Int { self["unsupported"] }
    /// The stat that pays for itself in the UI: `> 0` means macOS denied the
    /// *server* process a read, which is a Full Disk Access prompt, not a bug.
    var permissionDenied: Int { self["permission_denied"] }
    var chunks: Int { self["chunks"] }
    /// Documents swept because the walk no longer saw them (stale reconciliation).
    var removed: Int { self["removed"] }

    /// Mirrors `jobs.py:_FILE_STAT_KEYS` — everything except `chunks`, whose
    /// counter belongs to the embed phase.
    var filesSeen: Int {
        indexed + skipped + failed + unsupported + permissionDenied
    }

    /// Key-wise sum, for aggregating a multi-root reindex. Counters are
    /// per-root totals over disjoint trees, so adding them is the right
    /// arithmetic — the caveat is nested roots, which would count a shared
    /// subtree twice (see `IndexViewModel.aggregateStats`).
    static func sum(_ all: [IndexStats]) -> IndexStats {
        var merged: [String: Int] = [:]
        for stats in all {
            for (key, value) in stats.values { merged[key, default: 0] += value }
        }
        return IndexStats(values: merged)
    }
}

// MARK: - SSE events

/// `snapshot` — sent once on connect, reflecting current state. Re-attach is
/// first-class in the contract, so this is a full progress update, not a
/// preamble: decode it exactly like a `progress`.
struct IndexSnapshot: Codable, Sendable {
    let jobID: String?
    let status: String  // running | done | cancelled | error | idle
    let root: String?
    let phase: String?
    let stats: IndexStats
    let done: Int
    let total: Int?
    let error: String?

    // `started_at` / `finished_at` are on the wire but not decoded — nothing in
    // this step renders them, and Codable ignores unknown keys.
    private enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case status
        case root
        case phase
        case stats
        case done
        case total
        case error
    }
}

/// `progress` — absolute counts, never deltas (delivery is throttled and
/// droppable, so absolutes self-heal where deltas would desync forever).
struct IndexProgress: Codable, Sendable {
    let jobID: String
    /// `scan` | `embed` | `reconciling`. **This**, not `total == nil`, is what
    /// tells "still walking, count unknown" from "embedding, count known".
    let phase: String
    let stats: IndexStats
    let done: Int
    /// `null` during `scan`: the walk is a lazy generator, so the file count
    /// isn't known until it finishes.
    let total: Int?

    private enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case phase
        case stats
        case done
        case total
    }
}

/// `done` and `cancelled` share a shape: `{type, job_id, stats}`. `done`'s stats
/// are final and authoritative; `cancelled`'s are partial (committed work stays
/// committed — indexing is incremental, so the next run picks up the rest).
struct IndexTerminal: Codable, Sendable {
    let jobID: String
    let stats: IndexStats

    private enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case stats
    }
}

/// `error` — `{type, job_id, message}`. No stats.
struct IndexFailure: Codable, Sendable {
    let jobID: String
    let message: String

    private enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case message
    }
}

/// One decoded event off `GET /api/index/events`, dispatched on the `type`
/// discriminator inside the JSON `data:` (the server also sends an SSE `event:`
/// line carrying the same value — a superset, so either dispatch style works;
/// keying off the JSON keeps framing and payload from having to agree).
enum IndexEvent: Sendable {
    case snapshot(IndexSnapshot)
    case progress(IndexProgress)
    case done(IndexTerminal)
    case cancelled(IndexTerminal)
    case failed(IndexFailure)
    /// A `type` this client doesn't know. Kept as a case rather than a thrown
    /// error so a future server-side event can't kill a live progress stream.
    case unknown(String)

    /// The stream closes after any of these.
    var isTerminal: Bool {
        switch self {
        case .done, .cancelled, .failed: true
        case .snapshot, .progress, .unknown: false
        }
    }

    /// The job this event belongs to, when it names one. Used to ignore events
    /// from a job that isn't the one this view model started.
    var jobID: String? {
        switch self {
        case .snapshot(let event): event.jobID
        case .progress(let event): event.jobID
        case .done(let event), .cancelled(let event): event.jobID
        case .failed(let event): event.jobID
        case .unknown: nil
        }
    }
}

extension IndexEvent: Decodable {
    private enum DiscriminatorKey: String, CodingKey {
        case type
    }

    init(from decoder: Decoder) throws {
        let type = try decoder.container(keyedBy: DiscriminatorKey.self).decode(String.self, forKey: .type)
        switch type {
        case "snapshot": self = .snapshot(try IndexSnapshot(from: decoder))
        case "progress": self = .progress(try IndexProgress(from: decoder))
        case "done": self = .done(try IndexTerminal(from: decoder))
        case "cancelled": self = .cancelled(try IndexTerminal(from: decoder))
        case "error": self = .failed(try IndexFailure(from: decoder))
        default: self = .unknown(type)
        }
    }
}

// MARK: - SSE framing

/// Accumulates `text/event-stream` bytes into complete event payloads.
///
/// **One line is not one event.** An SSE message is a run of field lines
/// terminated by a *blank* line, so `data:` content is buffered until that
/// blank line arrives and only then handed off for decoding. Two rules this
/// parser exists to enforce:
///
/// - Lines beginning with `:` are comments and are dropped, never decoded. The
///   server sends `: ping` every 15 s (`jobs.py:HEARTBEAT_S`); feeding it to
///   `JSONDecoder` would produce a spurious error every heartbeat.
/// - `event:` / `id:` / `retry:` fields are ignored — dispatch is on the `type`
///   inside the JSON.
///
/// That heartbeat is also what keeps the connection alive: data every 15 s
/// resets `URLSession`'s inactivity timer, which is why the consumer must not
/// impose a timeout shorter than the interval.
///
/// **This splits lines itself rather than using `AsyncBytes.lines`, and that is
/// not a style choice.** Foundation's `AsyncLineSequence` *drops empty lines* —
/// which in SSE is precisely the byte that terminates an event. Consumed
/// through `.lines`, a perfectly well-formed stream yields field lines that are
/// never flushed, so no event ever decodes and the stream just ends: the sheet
/// reads "lost the progress stream" while the server is happily publishing.
/// Measured, not theorized — it is what the first run of this flow did.
struct SSEFrameParser {
    private var dataLines: [String] = []
    private var lineBuffer: [UInt8] = []

    private static let lineFeed = UInt8(ascii: "\n")

    /// Feed one byte off the stream; returns a complete event payload when that
    /// byte closed a message.
    mutating func consume(_ byte: UInt8) -> String? {
        guard byte == Self.lineFeed else {
            lineBuffer.append(byte)
            return nil
        }
        let line = String(decoding: lineBuffer, as: UTF8.self)
        lineBuffer.removeAll(keepingCapacity: true)
        return feed(line)
    }

    /// Feed one line; returns the complete event payload when this line
    /// terminated a message, `nil` otherwise.
    mutating func feed(_ rawLine: String) -> String? {
        // SSE permits CRLF; `AsyncBytes.lines` splits on the LF, so strip any
        // stray CR before it reaches the JSON decoder.
        let line = rawLine.hasSuffix("\r") ? String(rawLine.dropLast()) : rawLine

        if line.isEmpty {
            guard !dataLines.isEmpty else { return nil }  // blank line, nothing buffered
            let payload = dataLines.joined(separator: "\n")
            dataLines.removeAll(keepingCapacity: true)
            return payload
        }

        if line.hasPrefix(":") { return nil }  // comment — the `: ping` heartbeat

        guard let separator = line.firstIndex(of: ":") else { return nil }  // field with no value
        let field = line[line.startIndex..<separator]
        guard field == "data" else { return nil }  // event / id / retry — not ours

        var value = String(line[line.index(after: separator)...])
        if value.hasPrefix(" ") { value.removeFirst() }  // spec: one optional leading space
        dataLines.append(value)
        return nil
    }
}
