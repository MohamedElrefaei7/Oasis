//
//  StatusModels.swift
//  Oasis
//
//  Wire mirror of `GET /api/status` (`src/oasis/api/schemas.py:StatusResponse`),
//  the token-gated detail view of the on-disk index. Field names read off the
//  source, not invented.
//
//  `404` when no index exists at `db_path`; `200` with `documents: 0` when the
//  index exists but is empty. Neither is an error — see `StatusViewModel`.
//

import Foundation

struct StatusResponse: Codable, Sendable {
    let documents: Int
    let dbSizeBytes: Int
    /// UTC ISO 8601 with an offset; `null` if never indexed.
    let lastIndexedAt: Date?
    let dbPath: String

    // MARK: Capability fields
    //
    // Mirrors `/api/health` — the server derives both from the same
    // `get_capabilities()`, so the two endpoints can't disagree.

    /// The index's recorded schema version; `0` when absent (legacy index).
    let schemaVersion: Int
    let vectorsBuilt: Bool
    let embeddingModel: String?
    let embeddingDimension: Int?
    /// `vectorsBuilt` **and** built at the dimension the live embedder uses. A
    /// dimension mismatch means stored vectors exist but are unusable.
    let semanticReady: Bool
    /// Derived server-side — the client does no version math. The granular
    /// fields above are kept so the app can *word* the prompt.
    let reindexRecommended: Bool

    /// Absolute directory roots this index covers. Empty means "unknown
    /// coverage" (an index predating root tracking), not "covers nothing".
    let indexedRoots: [String]

    /// Indexed documents whose file is gone from disk.
    ///
    /// **`nil` means "not computed", which is not the same as `0`.** Over
    /// `STALE_SCAN_CAP` (5000) documents the server skips the per-file stat scan
    /// as too costly and reports null; `0` means it ran and found none. The
    /// panel must render those differently or it will claim a clean index it
    /// never checked.
    let staleDocuments: Int?

    private enum CodingKeys: String, CodingKey {
        case documents
        case dbSizeBytes = "db_size_bytes"
        case lastIndexedAt = "last_indexed_at"
        case dbPath = "db_path"
        case schemaVersion = "schema_version"
        case vectorsBuilt = "vectors_built"
        case embeddingModel = "embedding_model"
        case embeddingDimension = "embedding_dimension"
        case semanticReady = "semantic_ready"
        case reindexRecommended = "reindex_recommended"
        case indexedRoots = "indexed_roots"
        case staleDocuments = "stale_documents"
    }
}

// MARK: - Decoding

extension JSONDecoder {

    /// A decoder for `/api/status`.
    ///
    /// **`.iso8601` is not sufficient here, and the reason is easy to miss.**
    /// `CLAUDE.md` § Wire conventions established that every datetime carries a
    /// UTC offset precisely so Swift's `.iso8601` strategy can decode it — that
    /// part holds. But `.iso8601` is `ISO8601DateFormatter` with
    /// `.withInternetDateTime` alone, which **rejects fractional seconds**, and
    /// `last_indexed_at` is built from a float Unix mtime:
    ///
    ///     2026-07-26T20:34:38.109630+00:00   ← what the server actually sends
    ///     2026-07-25T17:20:00+00:00          ← what it sends on a whole second
    ///
    /// So the format varies with the *value*: a timestamp that lands exactly on
    /// a second decodes under `.iso8601` and one that doesn't fails. That is the
    /// worst kind of bug — it works until it doesn't, and never in a way a
    /// single test would catch. Both forms are accepted here.
    static var oasisStatus: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let text = try decoder.singleValueContainer().decode(String.self)
            if let date = iso8601Fractional.date(from: text) { return date }
            if let date = iso8601Plain.date(from: text) { return date }
            throw DecodingError.dataCorrupted(
                DecodingError.Context(
                    codingPath: decoder.codingPath,
                    debugDescription: "Not an ISO 8601 datetime with an offset: \(text)"
                )
            )
        }
        return decoder
    }

    private static let iso8601Fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let iso8601Plain: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
}
