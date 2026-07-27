//
//  DocumentOpener.swift
//  Oasis
//
//  Clicking a result opens the file in whatever app owns it — Preview for a
//  PDF, Pages for a `.docx`, and so on.
//
//  **Through `POST /api/open`, not `NSWorkspace.shared.open(_:)`.** The local
//  call is one line and would work, which is exactly why the reason for not
//  using it needs writing down:
//
//  1. **The index is the authority on what Oasis may open.** The endpoint looks
//     the path up with `KeywordIndex.get_doc_id` before it shells out, and that
//     lookup *is* the security boundary (`api/open.py`). Opening locally moves
//     the decision into the client and quietly drops the check.
//  2. **404 and 410 are different answers and the app should say so.**
//     `NSWorkspace` returns one boolean. The server distinguishes "that path
//     isn't in the index" from "it is indexed, and it's gone from disk" — and
//     the second is the actionable one, because a reindex clears it.
//  3. One engine, many front-ends: `oasis open` and the app take the same path.
//

import Foundation
import Observation
import OSLog

@MainActor
@Observable
final class DocumentOpener {

    /// A refusal worth putting on screen. `path` is carried so the banner can
    /// name the file rather than the whole failure.
    struct Failure: Equatable {
        let message: String
        let path: String

        var filename: String { URL(fileURLWithPath: path).lastPathComponent }
    }

    /// Paths with a request in flight. Keyed by path rather than a single
    /// boolean because the grid shows eight cards and only the clicked one
    /// should show that it's working.
    private(set) var opening: Set<String> = []

    /// The most recent refusal. Cleared by the next successful open, or by the
    /// user dismissing the banner.
    var failure: Failure?

    private static let log = Logger(subsystem: "com.oasis.app", category: "open")

    private let controller: ServerController
    private let session: URLSession

    init(controller: ServerController) {
        self.controller = controller
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.waitsForConnectivity = false
        self.session = URLSession(configuration: configuration)
    }

    func isOpening(_ result: SearchResult) -> Bool {
        opening.contains(result.path)
    }

    /// Open a result. A second click while the first is in flight is dropped —
    /// launching the same document twice is the classic double-fire bug, and
    /// the round trip is short enough that a user can easily out-click it.
    func open(_ result: SearchResult) {
        let path = result.path
        guard !opening.contains(path) else {
            Self.log.debug("open already in flight, ignoring the second click")
            return
        }

        opening.insert(path)
        Task { @MainActor [weak self] in
            await self?.perform(path)
            self?.opening.remove(path)
        }
    }

    private func perform(_ path: String) async {
        guard let handshake = controller.handshake else {
            failure = Failure(message: "The Oasis server isn't running.", path: path)
            return
        }

        var components = URLComponents()
        components.scheme = "http"
        components.host = "127.0.0.1"
        components.port = handshake.port
        components.path = "/api/open"

        guard let url = components.url else {
            failure = Failure(message: "Couldn't build the open URL.", path: path)
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // The path goes over as JSON, never interpolated into a URL: it can
        // contain anything a filename can, and the server matches the *exact*
        // stored form — any mangling here is a 404 on a file that exists.
        request.httpBody = try? JSONEncoder().encode(["path": path])

        do {
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0

            switch status {
            case 204:
                Self.log.notice("opened \(path, privacy: .private)")
                failure = nil

            // Indexed once, gone from disk now. The only failure with a fix the
            // user can act on, so it names it.
            case 410:
                Self.log.notice("open 410 — indexed but missing from disk")
                failure = Failure(
                    message: "This file has moved or been deleted since Oasis indexed it. Reindex Current Folders to clear it from your results.",
                    path: path
                )

            case 404:
                Self.log.error("open 404 — path is not in the index")
                failure = Failure(
                    message: "Oasis can't open this file — it isn't in the index any more.",
                    path: path
                )

            default:
                let detail = Self.decodeErrorMessage(from: data) ?? "the server returned HTTP \(status)."
                Self.log.error("open failed (\(status)): \(detail, privacy: .public)")
                failure = Failure(message: "Couldn't open this file — \(detail)", path: path)
            }
        } catch {
            Self.log.error("open error: \(error.localizedDescription, privacy: .public)")
            failure = Failure(
                message: "Couldn't open this file — \(error.localizedDescription)",
                path: path
            )
        }
    }

    /// The `{error: {code, message}}` envelope every endpoint uses.
    private static func decodeErrorMessage(from data: Data) -> String? {
        try? JSONDecoder().decode(ErrorResponse.self, from: data).error.message
    }
}
