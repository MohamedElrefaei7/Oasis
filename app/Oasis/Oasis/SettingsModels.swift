//
//  SettingsModels.swift
//  Oasis
//
//  The preference keys Settings writes and the rest of the app reads, plus the
//  wire mirror of `POST /api/index/remove-root`.
//
//  **What is deliberately NOT here.** Settings holds per-user preferences and
//  recourse — not knobs that re-litigate settled measurements. Search mode
//  (hybrid), reranking (on), and `raw`/NL-parsing (raw; parsing costs −0.108
//  ndcg@10) were decided by the eval matrix, and every alternative is *worse*
//  on the numbers. Exposing them would invite users into configurations the
//  project measured and rejected, and then quietly attribute the resulting bad
//  results to Oasis. A preference is for something only the user can know.
//

import Foundation

// MARK: - Preferences

/// The `@AppStorage`/`UserDefaults` keys, in one place.
///
/// Views bind with `@AppStorage`; non-view readers (`SearchViewModel`) read
/// `UserDefaults` directly. Both need the same string, and a typo in either
/// half is a silently-ignored preference — hence one enum rather than literals
/// at each site.
enum PreferenceKey {
    /// How many results a search asks the server for.
    static let resultLimit = "resultLimit"
}

/// Values and clamping for the `resultLimit` preference.
///
/// A preset picker, not a free number field: the honest range is small, the
/// grid is two columns, and "how many results" is a taste question with no
/// wrong answer inside a sane range — which is exactly what a preference is
/// for, unlike the retrieval knobs above.
enum ResultLimit {
    static let presets = [4, 8, 12, 16]

    /// The sketch's max, and what the app shipped with hardcoded.
    static let fallback = 8

    /// The stored preference, or the default.
    ///
    /// `UserDefaults.integer(forKey:)` returns **0** for an absent key, which is
    /// indistinguishable from a stored 0 and would ask the server for zero
    /// results. Anything outside the presets — an unset key, a value left over
    /// from an older build, a hand-edited plist — falls back rather than being
    /// passed through.
    static var current: Int {
        let stored = UserDefaults.standard.integer(forKey: PreferenceKey.resultLimit)
        return presets.contains(stored) ? stored : fallback
    }
}

// MARK: - POST /api/index/remove-root

/// Body of `POST /api/index/remove-root`.
struct RemoveRootRequest: Codable, Sendable {
    let root: String
}

/// `200` body of `POST /api/index/remove-root`.
///
/// `root` comes back **abspath'd by the server**, which is not merely an echo:
/// the server normalizes before matching, so this is the stored form it
/// actually acted on rather than the spelling the client sent.
///
/// `removed` can legitimately be `0` for a tracked root whose documents were
/// already swept away, and that is still a success — untracking the root is the
/// point of the call. The UI wording has to survive that case without reading
/// as a failure.
struct RemoveRootResponse: Codable, Sendable {
    let root: String
    let removed: Int
}
