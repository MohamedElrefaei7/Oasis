//
//  SearchModels.swift
//  Oasis
//
//  Wire mirrors of `GET /api/search` and the error envelope. Field names are
//  taken from src/oasis/api/schemas.py (`SearchResponse`, `SearchResult`,
//  `Segment`, `ErrorResponse`) — not invented.
//

import Foundation

/// One run of snippet text. `src/oasis/api/schemas.py:Segment`.
///
/// **Segments, not `{start, end}` offsets** — the format was chosen precisely
/// so no integer offset ever crosses this boundary. Python indexes strings by
/// codepoint and Swift by grapheme cluster (and `NSRange`/`AttributedString` by
/// UTF-16), so any offset would need a conversion sitting exactly where nobody
/// tests: emoji, accents, CJK. Concatenating `text` in order reproduces the
/// snippet, and that is all the client has to do.
struct Segment: Codable, Sendable, Hashable {
    let text: String
    let match: Bool
}

/// `src/oasis/api/schemas.py:SearchResult`.
struct SearchResult: Codable, Sendable, Identifiable, Hashable {
    let path: String
    let title: String?
    let docId: Int
    let score: Double
    let snippet: [Segment]

    var id: Int { docId }

    private enum CodingKeys: String, CodingKey {
        case path
        case title
        case docId = "doc_id"
        case score
        case snippet
    }

    /// `title` is `str | None` server-side and can also be empty; fall back to
    /// the filename so a card is never headless.
    var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return title
        }
        return URL(fileURLWithPath: path).lastPathComponent
    }

    var fileURL: URL { URL(fileURLWithPath: path) }
}

/// `src/oasis/api/schemas.py:SearchResponse`.
///
/// `parsed` (the full `ParsedQuery`) is deliberately **not** decoded: it's what
/// drives the filter chips in a later step, and `Codable` ignores unknown keys,
/// so tolerating it costs nothing today. `mode`/`llm_parsed`/`latency_ms` are
/// decoded but only logged — the server's `latency_ms` is the honest warm
/// retrieval number and worth having in the console.
struct SearchResponse: Codable, Sendable {
    let results: [SearchResult]
    let mode: String
    let llmParsed: Bool
    let latencyMs: Double
    let dbPath: String

    private enum CodingKeys: String, CodingKey {
        case results
        case mode
        case llmParsed = "llm_parsed"
        case latencyMs = "latency_ms"
        case dbPath = "db_path"
    }
}

/// The one error shape every endpoint uses:
/// `{"error": {"code": "...", "message": "..."}}`.
struct ErrorResponse: Codable, Sendable {
    struct Detail: Codable, Sendable {
        let code: String
        let message: String
    }

    let error: Detail
}
