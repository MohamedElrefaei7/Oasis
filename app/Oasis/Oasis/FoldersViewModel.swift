//
//  FoldersViewModel.swift
//  Oasis
//
//  Settings ▸ Folders. Lists `indexed_roots` and removes one through
//  `POST /api/index/remove-root`.
//
//  **Removal is destructive and asymmetric with the rest of Settings**, so it
//  is modelled like Reset rather than like a preference: a confirm that names
//  the stakes, a disabled control while an index job runs (the server would
//  409), and a refresh of *both* health and status afterwards, because the
//  document count on the main window's statistics panel just changed.
//
//  It shares the one `StatusViewModel` with the main window rather than
//  fetching its own — the folder list here and the folder list in the
//  statistics panel are the same list, and two readers would let them disagree
//  the moment a folder is removed.
//

import Foundation
import Observation
import OSLog

@MainActor
@Observable
final class FoldersViewModel {

    /// Set when a removal was refused or failed. Cleared on the next attempt.
    private(set) var message: String?

    /// The root currently being removed, so its row alone can show progress.
    /// Path rather than `Bool` because the list is the UI, and a spinner on the
    /// whole tab would say less than one on the row that is going away.
    private(set) var removing: String?

    private static let log = Logger(subsystem: "com.oasis.app", category: "folders")

    private let coordinator: AppSearchCoordinator
    private let session: URLSession

    private var controller: ServerController { coordinator.controller }
    private var status: StatusViewModel { coordinator.status }

    /// The roots, straight off the shared status model.
    var roots: [String] { status.indexedRoots }

    init(coordinator: AppSearchCoordinator) {
        self.coordinator = coordinator
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.waitsForConnectivity = false
        self.session = URLSession(configuration: configuration)
    }

    func refresh() {
        status.refresh()
    }

    // MARK: - Remove

    /// Remove one indexed folder.
    ///
    /// **The caller owns the human confirmation** — this fires immediately, the
    /// same contract `IndexViewModel.reset()` has. Deleting a folder's indexed
    /// documents is irreversible (a reindex can rebuild them, but only if the
    /// files are still on disk — and the case this feature exists for is
    /// precisely the one where they aren't), so it must never be reachable
    /// without a destructive dialog in front of it.
    func remove(_ root: String) {
        guard removing == nil else { return }
        removing = root
        message = nil

        Task { @MainActor [weak self] in
            await self?.performRemove(root)
            self?.removing = nil
        }
    }

    private func performRemove(_ root: String) async {
        guard let handshake = controller.handshake,
              let url = IndexRunner.endpoint(port: handshake.port, path: "/api/index/remove-root")
        else {
            message = "The server isn't running."
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(handshake.token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.httpBody = try? JSONEncoder().encode(RemoveRootRequest(root: root))

        Self.log.notice("POST /api/index/remove-root \(root, privacy: .public)")

        do {
            let (data, response) = try await session.data(for: request)
            switch (response as? HTTPURLResponse)?.statusCode ?? 0 {
            case 200:
                let decoded = try? JSONDecoder().decode(RemoveRootResponse.self, from: data)
                let removed = decoded?.removed ?? 0
                Self.log.notice("removed \(removed) document(s) under \(root, privacy: .public)")
                await refreshAfterChange()

            case 404:
                // The root isn't tracked. The app's list was ahead of the
                // server's — nothing is wrong, and re-reading is a better answer
                // than an error the user can't act on. Same reasoning as reset's
                // 404 branch.
                Self.log.notice("remove-root 404 — \(root, privacy: .public) was not a tracked root")
                await refreshAfterChange()

            case 409:
                // Shares the job lock with /api/index. The control is disabled
                // while a job runs, so this is the defensive path — a job
                // started elsewhere, or a race with the disable.
                message = IndexRunner.errorMessage(from: data)
                    ?? "An index is running — cancel or wait before removing a folder."
                Self.log.error("remove-root refused (409): \(self.message ?? "", privacy: .public)")

            case let httpStatus:
                let detail = IndexRunner.errorMessage(from: data) ?? "the server returned HTTP \(httpStatus)."
                Self.log.error("remove-root failed (\(httpStatus)): \(detail, privacy: .public)")
                message = "Couldn't remove the folder — \(detail)"
            }
        } catch {
            Self.log.error("remove-root request failed: \(error.localizedDescription, privacy: .public)")
            message = "Couldn't remove the folder — \(error.localizedDescription)"
        }
    }

    /// The same pair `IndexViewModel.finish()` runs, and for the same reason:
    /// removing a folder changes `documents`, so the app's held `HealthResponse`
    /// *and* its `/api/status` payload both describe a world that no longer
    /// exists — the statistics panel's count, the folder list, and the search
    /// area's empty state all read from them.
    private func refreshAfterChange() async {
        await controller.refreshHealth()
        await status.refreshAndWait()
        // And the search area behind the Settings window, which is usually still
        // on screen. Removing the last folder's documents is a live producer of
        // the empty-index onboarding state, and a stale grid of results that no
        // longer exist is the one outcome worse than an empty one — clicking one
        // would 404 through `/api/open`.
        coordinator.search.indexDidChange()
    }
}
