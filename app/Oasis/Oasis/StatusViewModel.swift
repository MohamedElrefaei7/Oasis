//
//  StatusViewModel.swift
//  Oasis
//
//  The one owner of `GET /api/status`. Follows `ServerController.refreshHealth()`
//  — read, decode, republish — with the difference that this one carries a
//  render state, because the panel has something to show while loading and
//  something calm to show when there's no index.
//
//  It is also what the Reindex action reads its roots from, so the app has a
//  single status fetch rather than one per consumer.
//

import Foundation
import Observation
import OSLog

@MainActor
@Observable
final class StatusViewModel {

    /// **`404` and `200 documents: 0` are both "empty", never an error.**
    /// A never-indexed DB and a reset one are two shapes of "no content yet"
    /// (APP_SEAM.md §6e); a panel that renders either in red says Oasis is
    /// broken when it is merely new.
    enum State {
        case loading
        /// The index has documents.
        case loaded(StatusResponse)
        /// No documents. The payload is `nil` for a `404` (no index on disk at
        /// all, so there is nothing to describe) and present for a `200` with
        /// `documents: 0` — which still carries a `db_path` and, notably, may
        /// still carry **roots**: the pipeline records a root *before* it walks,
        /// so indexing a folder that yields nothing leaves exactly this state.
        case empty(StatusResponse?)
        /// The request itself failed — unreachable server, bad token, undecodable
        /// body. Distinct from "no index", and the only case worth an error tone.
        case failed(String)
    }

    private(set) var state: State = .loading

    /// The payload behind whichever state carries one.
    var status: StatusResponse? {
        switch state {
        case .loaded(let status): status
        case .empty(let status): status
        case .loading, .failed: nil
        }
    }

    /// Roots the index covers — what Reindex re-scans, and what the panel lists.
    var indexedRoots: [String] { status?.indexedRoots ?? [] }

    private static let log = Logger(subsystem: "com.oasis.app", category: "status")

    private let controller: ServerController
    private let session: URLSession
    private var refreshTask: Task<Void, Never>?

    init(controller: ServerController) {
        self.controller = controller
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.waitsForConnectivity = false
        self.session = URLSession(configuration: configuration)
    }

    /// Fire-and-forget refresh, for view lifecycle callbacks.
    func refresh() {
        refreshTask?.cancel()
        refreshTask = Task { @MainActor [weak self] in
            await self?.reload()
        }
    }

    /// Refresh and wait — used by the index flow, which must not report
    /// completion until the panel it is about to reveal is current.
    func refreshAndWait() async {
        refreshTask?.cancel()
        await reload()
    }

    private func reload() async {
        guard let handshake = controller.handshake,
              let url = IndexRunner.endpoint(port: handshake.port, path: "/api/status")
        else {
            state = .failed("The server isn't running.")
            return
        }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        request.cachePolicy = .reloadIgnoringLocalCacheData

        do {
            let (data, response) = try await session.data(for: request)
            guard !Task.isCancelled else { return }
            let httpStatus = (response as? HTTPURLResponse)?.statusCode ?? 0

            switch httpStatus {
            case 200:
                let decoded = try JSONDecoder.oasisStatus.decode(StatusResponse.self, from: data)
                state = decoded.documents > 0 ? .loaded(decoded) : .empty(decoded)
                Self.log.debug(
                    "status: documents=\(decoded.documents) roots=\(decoded.indexedRoots.count) stale=\(decoded.staleDocuments.map(String.init) ?? "not computed", privacy: .public) reindex_recommended=\(decoded.reindexRecommended)"
                )

            case 404:
                // No index at db_path. The not-found answer to "describe the
                // index", not a failure of the app.
                Self.log.debug("status: 404 — no index exists yet")
                state = .empty(nil)

            default:
                let message = IndexRunner.errorMessage(from: data) ?? "the server returned HTTP \(httpStatus)."
                Self.log.error("status failed (\(httpStatus)): \(message, privacy: .public)")
                state = .failed(message)
            }
        } catch {
            guard !Task.isCancelled else { return }
            Self.log.error("status fetch failed: \(error.localizedDescription, privacy: .public)")
            state = .failed(error.localizedDescription)
        }
    }
}
