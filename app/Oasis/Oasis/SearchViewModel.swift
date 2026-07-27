//
//  SearchViewModel.swift
//  Oasis
//
//  The query bar's state machine and the first authenticated call the app
//  makes. Reads `port`/`token` off the handshake `ServerController` already
//  stashed in step 1 — `/api/health` needed no auth, `/api/search` does.
//

import Foundation
import Observation
import OSLog

@MainActor
@Observable
final class SearchViewModel {

    /// The result area's states. `.empty` and `.noMatches` are deliberately
    /// distinct — see `refreshRestingState()`.
    enum SearchState {
        /// Ready, index has content, nothing typed yet.
        case idle
        case searching
        case results([SearchResult])
        /// The index has content, but this query matched nothing.
        case noMatches(String)
        /// The *index* is empty (never indexed, or reset). Onboarding, not error.
        case empty
        case failed(String)
    }

    var query: String = "" {
        didSet {
            // Editing the query invalidates a highlight that points into the
            // *previous* answer. Guarded because this fires on every keystroke
            // and a redundant write to an `@Observable` property invalidates
            // every view observing it.
            if selectedIndex != nil { selectedIndex = nil }
        }
    }

    private(set) var state: SearchState = .idle {
        // One place to reset the keyboard highlight, rather than a `nil` next
        // to every assignment — and there are six. An index into the old result
        // array is meaningless the instant the array is replaced, and the
        // out-of-bounds crash it would otherwise cause is exactly the kind of
        // bug that only shows up when a slow search lands under a fast finger.
        didSet { selectedIndex = nil }
    }

    /// Which result the keyboard is on, as an index into `results`.
    ///
    /// `nil` means "no highlight" — the resting state, and the state Return
    /// treats as "run the search" rather than "open something".
    private(set) var selectedIndex: Int?

    /// Sketch max, and what `limit` is pinned to.
    static let resultLimit = 8

    private static let log = Logger(subsystem: "com.oasis.app", category: "search")

    private let controller: ServerController
    private var searchTask: Task<Void, Never>?
    private let session: URLSession

    init(controller: ServerController) {
        self.controller = controller
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.waitsForConnectivity = false
        self.session = URLSession(configuration: configuration)
    }

    // MARK: - Keyboard selection

    /// The results currently on screen, or empty in every other state.
    var results: [SearchResult] {
        if case .results(let results) = state { return results }
        return []
    }

    /// The highlighted result, if any. Bounds-checked rather than trusted:
    /// `selectedIndex` and `results` are separate properties, and a subscript
    /// on a stale index is a crash rather than a wrong answer.
    var selectedResult: SearchResult? {
        guard let selectedIndex, results.indices.contains(selectedIndex) else { return nil }
        return results[selectedIndex]
    }

    enum MoveDirection {
        case up, down, left, right
    }

    /// Move the highlight. Returns whether the key press was consumed, so the
    /// caller can hand an unused arrow back to the text field.
    ///
    /// `columns` comes from the view because the grid's shape is the view's
    /// business — but the arithmetic lives here, in one place, rather than
    /// being spelled out at four key handlers.
    ///
    /// The result grid fills **row-major** (left to right, top to bottom, which
    /// *is* the ranking), so ← → step by one and ↑ ↓ step by a whole row.
    @discardableResult
    func moveSelection(_ direction: MoveDirection, columns: Int) -> Bool {
        let count = results.count
        guard count > 0, columns > 0 else { return false }

        guard let current = selectedIndex, results.indices.contains(current) else {
            // Nothing highlighted yet. ↓ and → enter the results at the top;
            // ↑ and ← have nowhere to come from, so they stay in the text field
            // where the caret can use them.
            switch direction {
            case .down, .right:
                selectedIndex = 0
                return true
            case .up, .left:
                return false
            }
        }

        let target: Int
        switch direction {
        case .up:
            // Leaving the top row gives the highlight back rather than sticking
            // to it — ↑ from the first row returns the user to a plain query,
            // which is the only way out of the grid that doesn't need the mouse.
            if current < columns {
                selectedIndex = nil
                return true
            }
            target = current - columns
        case .down:
            target = current + columns
        case .left:
            target = current - 1
        case .right:
            target = current + 1
        }

        // Clamp, never wrap. Wrapping from the last result back to the first is
        // disorienting in a grid, and a ↓ that lands on the last row should stay
        // there rather than teleport to the top.
        selectedIndex = min(max(target, 0), count - 1)
        return true
    }

    /// Point the highlight at a specific result — the click path, so the
    /// keyboard picks up where the mouse left off.
    func select(_ index: Int) {
        guard results.indices.contains(index) else { return }
        selectedIndex = index
    }

    /// Returns whether there was anything to clear, so Escape can fall through
    /// to whatever else Escape might mean when nothing is highlighted.
    @discardableResult
    func clearSelection() -> Bool {
        guard selectedIndex != nil else { return false }
        selectedIndex = nil
        return true
    }

    // MARK: - Resting state

    /// Decide between the two "nothing to show" states when there's no query.
    ///
    /// APP_SEAM.md §6e is explicit that these are different things, and that
    /// conflating them is the "a wiped index looks broken" failure:
    /// - `documents` **null** (never indexed) or **0** (indexed then reset) →
    ///   `.empty`, an onboarding prompt. Both shapes mean "no content yet".
    /// - `documents > 0` → `.idle`, a blank area waiting for a query.
    ///
    /// Read off the `HealthResponse` step 1's poll already holds — no fetch.
    ///
    /// **Only applies when there is no query.** Since step 7 this view model is
    /// app-level and outlives the window, so `ContentView.onAppear` fires on a
    /// model that may already hold a query and its results — a window reopened
    /// by the summon hand-off is exactly that case. Without this guard, opening
    /// the window would blank the results the hand-off just asked for.
    func refreshRestingState() {
        guard query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            Self.log.debug("resting state skipped — a query is up")
            return
        }

        let documents = controller.health?.documents
        let isEmptyIndex = (documents ?? 0) == 0
        state = isEmptyIndex ? .empty : .idle
        Self.log.debug("resting state: \(isEmptyIndex ? "empty (no index content)" : "idle", privacy: .public) — documents=\(documents.map(String.init) ?? "null", privacy: .public)")
    }

    /// The index changed underneath us (an index or reindex finished).
    ///
    /// With no query up, this is just the resting-state question again. With one
    /// up, the results on screen describe the *old* index: reindex's
    /// reconciliation sweep deletes documents whose files are gone, and a new
    /// index adds matches, so re-running the query is the only way the grid can
    /// stay honest. `refreshRestingState()` alone would blank the results, which
    /// reads as the search having been forgotten.
    func indexDidChange() {
        if query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            refreshRestingState()
        } else {
            submit()
        }
    }

    // MARK: - Search

    /// Enter-to-submit. Not search-as-you-type: each search is a real round trip
    /// through torch inference, and per-keystroke firing would queue work the
    /// user never asked for.
    func submit() {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)

        // The endpoint 400s on whitespace-only `q` (min_length=1 lets it through
        // to `_fallback_query`, which rejects it). Don't spend a request to be
        // told that — just fall back to the resting state.
        guard !trimmed.isEmpty else {
            Self.log.debug("empty query — not searching")
            searchTask?.cancel()
            refreshRestingState()
            return
        }

        // A newer search always wins. Without this, a slow earlier response can
        // land after a faster later one and render stale results over fresh.
        searchTask?.cancel()
        state = .searching
        searchTask = Task { @MainActor [weak self] in
            await self?.performSearch(trimmed)
        }
    }

    private func performSearch(_ trimmed: String) async {
        guard let handshake = controller.handshake else {
            state = .failed("The server isn't running.")
            return
        }

        // URLComponents, never string interpolation: queries carry spaces,
        // punctuation and unicode, and a hand-built "?q=\(query)" breaks on the
        // first `&`, `+`, `#` or space.
        var components = URLComponents()
        components.scheme = "http"
        components.host = "127.0.0.1"
        components.port = handshake.port
        components.path = "/api/search"
        components.queryItems = [
            URLQueryItem(name: "q", value: trimmed),
            URLQueryItem(name: "mode", value: "hybrid"),
            URLQueryItem(name: "limit", value: String(Self.resultLimit)),
            // The eval-measured best path and the endpoint's own default: NL
            // parsing costs −0.108 ndcg@10. Don't ask for a parse.
            URLQueryItem(name: "raw", value: "true"),
        ]

        guard let url = components.url else {
            state = .failed("Couldn't build the search URL.")
            return
        }

        var request = URLRequest(url: url)
        // The first authenticated call in the app. Loopback binding isn't authn
        // on a shared machine; the token is what actually gates the API.
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        request.cachePolicy = .reloadIgnoringLocalCacheData

        do {
            let (data, response) = try await session.data(for: request)
            guard !Task.isCancelled else {
                Self.log.debug("search superseded, dropping response for: \(trimmed, privacy: .public)")
                return
            }

            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard (200..<300).contains(status) else {
                let message = Self.decodeErrorMessage(from: data) ?? "the server returned HTTP \(status)."
                Self.log.error("search failed (\(status)): \(message, privacy: .public)")
                state = .failed("Search failed — \(message)")
                return
            }

            let decoded = try JSONDecoder().decode(SearchResponse.self, from: data)
            guard !Task.isCancelled else { return }

            if decoded.results.isEmpty {
                // An empty result set is a valid answer, not an error — and it
                // is NOT the same as an empty index.
                //
                // **Which of those two it is can't be read off the result set.**
                // Zero results over an empty index is the onboarding state, not
                // "your query matched nothing" — telling a user with no index
                // to "try different words" blames their query for a missing
                // index (APP_SEAM.md §6e). This is reachable the moment a
                // search is on screen when the index is emptied: Reset re-runs
                // the live query, and it came back `[]` against 0 documents.
                if (controller.health?.documents ?? 0) == 0 {
                    Self.log.notice("empty result over an empty index — onboarding, not no-matches")
                    state = .empty
                } else {
                    Self.log.notice("no matches for \(trimmed, privacy: .public) (\(String(format: "%.1f", decoded.latencyMs), privacy: .public)ms)")
                    state = .noMatches(trimmed)
                }
            } else {
                Self.log.notice("\(decoded.results.count) results for \(trimmed, privacy: .public) — mode=\(decoded.mode, privacy: .public) llm_parsed=\(decoded.llmParsed) server latency \(String(format: "%.1f", decoded.latencyMs), privacy: .public)ms")
                state = .results(decoded.results)
            }
        } catch is CancellationError {
            Self.log.debug("search cancelled: \(trimmed, privacy: .public)")
        } catch let error as URLError where error.code == .cancelled {
            Self.log.debug("search cancelled in flight: \(trimmed, privacy: .public)")
        } catch {
            guard !Task.isCancelled else { return }
            Self.log.error("search error: \(error.localizedDescription, privacy: .public)")
            state = .failed("Search failed — \(error.localizedDescription)")
        }
    }

    /// Pull `message` out of the `{error: {code, message}}` envelope every
    /// endpoint uses, so a failure reads as a sentence and not a status code.
    private static func decodeErrorMessage(from data: Data) -> String? {
        guard let envelope = try? JSONDecoder().decode(ErrorResponse.self, from: data) else {
            return nil
        }
        return envelope.error.message
    }
}
