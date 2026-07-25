//
//  ServerModels.swift
//  Oasis
//
//  The two payloads the app decodes off the `oasis serve` child process.
//  Ground truth: docs/APP_SEAM.md §2 (handshake) and §3/§6 (health).
//

import Foundation

/// The single line `oasis serve` writes to stdout after binding the socket and
/// before serving (APP_SEAM.md §2).
///
///     {"port": 51235, "token": "lp5pg…KN4", "pid": 19805}
///
/// Measured invariant: this is the *first* line on stdout, nothing precedes it,
/// and nothing follows it until shutdown. All logging goes to stderr.
struct Handshake: Codable, Sendable {
    let port: Int
    let token: String
    let pid: Int32
}

/// `GET /api/health` — no auth, always 200; the state lives in `status`
/// (APP_SEAM.md §3).
///
/// Only `status` is required here. Everything else is optional so that a
/// `loading` health — which the server fills with nulls/defaults until the
/// models finish — still decodes, and so a future field addition on the server
/// can't turn the readiness probe into a decode failure.
struct HealthResponse: Codable, Sendable {
    enum Status: String, Codable, Sendable {
        case loading
        case ready
        case error
    }

    let status: Status
    let version: String?
    /// `null` while loading, and `null` when no index exists at all.
    /// See `indexSummary` — `null` and `0` are two shapes of the same
    /// "no content yet" state (APP_SEAM.md §6e), not an error.
    let documents: Int?
    /// Populated when `status == .error`.
    let error: String?

    let vectorsBuilt: Bool?
    let embeddingModel: String?
    let embeddingDimension: Int?
    let semanticReady: Bool?
    let schemaVersion: Int?
    /// Derived server-side; the client does no version math.
    let reindexRecommended: Bool?

    private enum CodingKeys: String, CodingKey {
        case status
        case version
        case documents
        case error
        case vectorsBuilt = "vectors_built"
        case embeddingModel = "embedding_model"
        case embeddingDimension = "embedding_dimension"
        case semanticReady = "semantic_ready"
        case schemaVersion = "schema_version"
        case reindexRecommended = "reindex_recommended"
    }

    /// One line describing the index the server just reported.
    ///
    /// APP_SEAM.md §6e: `documents: null` (never indexed) and `documents: 0`
    /// (indexed then emptied, e.g. after `POST /api/reset`) are *both* "no
    /// content yet". A later step turns this into the offer-to-index flow; for
    /// now it just has to render both without pretending either is broken.
    var indexSummary: String {
        switch documents {
        case .none: "No index yet — nothing has been indexed"
        case .some(0): "Index is empty — 0 documents"
        case .some(let count): "\(count) document\(count == 1 ? "" : "s") indexed"
        }
    }
}
