//
//  OasisAPI.swift
//  Oasis
//
//  The one place the app knows how to talk to the local server: where it lives,
//  how a request is authorized, and how a failure is worded.
//
//  **Why this exists.** Six callers — search, status, index, reset, remove-root,
//  open — each built the same URL and the same `Authorization: Bearer` request
//  by hand. Three of them reached into `IndexRunner`'s statics for
//  `endpoint`/`errorMessage`, which made the *index runner* an accidental
//  dependency of the status panel and the folder list; three more carried
//  private copies of the same error-envelope decoder; and `ServerController`
//  built its health URLs by string interpolation while everything else used
//  `URLComponents`. None of that was wrong, and all of it was six places to
//  change for one edit — which is the shape of the problem when the server
//  moves from a dev-path binary to one inside the .app.
//
//  Mirrors the Python side's rule that all SQL lives in `KeywordIndex`: all
//  server addressing lives here.
//

import Foundation

enum OasisAPI {

    // MARK: - Addressing

    /// The server is loopback-only by construction (`api/serve.py` binds
    /// 127.0.0.1, never 0.0.0.0), so the host is not a setting.
    private static let host = "127.0.0.1"

    /// Build a URL for *path* on the local server.
    ///
    /// `URLComponents`, never string interpolation: query values carry spaces,
    /// punctuation and unicode, and a hand-built `"?q=\(query)"` breaks on the
    /// first `&`, `+`, `#` or space.
    static func url(port: Int, path: String, query: [URLQueryItem]? = nil) -> URL? {
        var components = URLComponents()
        components.scheme = "http"
        components.host = host
        components.port = port
        components.path = path
        components.queryItems = query
        return components.url
    }

    // MARK: - Requests

    /// An authorized request against the local server, or `nil` if the URL
    /// couldn't be built.
    ///
    /// The token is what actually gates the API — loopback binding is not
    /// authentication on a shared machine. `/api/health` is the one route that
    /// needs none, which is why `ServerController` builds that one without a
    /// handshake in hand.
    static func request(
        _ path: String,
        handshake: Handshake,
        method: String = "GET",
        query: [URLQueryItem]? = nil
    ) -> URLRequest? {
        guard let url = url(port: handshake.port, path: path, query: query) else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        // Always: a cached 200 for /api/status or /api/search would render an
        // index state that no longer exists.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        return request
    }

    /// An authorized request carrying a JSON body.
    ///
    /// Returns `nil` when the body can't be encoded, which is the same "can't
    /// build the request" outcome as a bad URL — callers already handle one
    /// `nil` and shouldn't grow a second failure shape for a case that cannot
    /// happen with the concrete `Encodable`s used here.
    static func request<Body: Encodable>(
        _ path: String,
        handshake: Handshake,
        method: String = "POST",
        json body: Body
    ) -> URLRequest? {
        guard var request = request(path, handshake: handshake, method: method) else { return nil }
        guard let encoded = try? JSONEncoder().encode(body) else { return nil }
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = encoded
        return request
    }

    // MARK: - Sessions

    /// An ephemeral session with no disk cache and no connectivity waiting.
    ///
    /// `waitsForConnectivity = false` throughout: the server is on loopback, so
    /// "wait for the network" can only ever mean "hang on a server that isn't
    /// there".
    static func session(timeout: TimeInterval) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }

    // MARK: - Errors

    /// Pull `message` out of the `{"error": {"code", "message"}}` envelope that
    /// every endpoint uses (CLAUDE.md § Error envelope).
    static func errorMessage(from data: Data) -> String? {
        try? JSONDecoder().decode(ErrorResponse.self, from: data).error.message
    }

    /// `errorMessage`, or a plain statement of the status code.
    ///
    /// Every caller wrote this `?? "the server returned HTTP \(status)."` line
    /// itself; the wording is part of user-facing sentences ("Couldn't remove
    /// the folder — …"), so it belongs in one place.
    static func failureDetail(from data: Data, status: Int) -> String {
        errorMessage(from: data) ?? "the server returned HTTP \(status)."
    }

    /// The status code of a response, or `0` when it isn't an HTTP response.
    static func statusCode(_ response: URLResponse?) -> Int {
        (response as? HTTPURLResponse)?.statusCode ?? 0
    }
}
