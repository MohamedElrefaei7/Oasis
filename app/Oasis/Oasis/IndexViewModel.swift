//
//  IndexViewModel.swift
//  Oasis
//
//  The index flow's state machine, over `IndexRunner` (which owns the POST + SSE
//  machinery for a single job).
//
//  **Both rail actions are the same sequence with a different root list**, which
//  is why there is one view model and not two: Index New Folder is the N = 1
//  case. The server runs one job at a time and 409s a second, so a multi-root
//  reindex is inherently sequential — run a root, await its terminal event,
//  advance. That is the only structural difference between the two operations.
//

import AppKit
import Foundation
import Observation
import OSLog

@MainActor
@Observable
final class IndexViewModel {

    // MARK: - Operation

    /// What the user asked for. Both send `force: false` — see `force`.
    enum Operation {
        /// One root, chosen through `NSOpenPanel`.
        case indexFolder(String)
        /// Every root the index already covers, from `/api/status.indexed_roots`.
        case reindexAll([String])

        var roots: [String] {
            switch self {
            case .indexFolder(let root): [root]
            case .reindexAll(let roots): roots
            }
        }

        /// **Always `false`, including for reindex.**
        ///
        /// A `force: false` run still does the *full walk*, which is where all
        /// of reindex's value is: the reconciliation sweep deletes documents the
        /// walk no longer sees, and the no-vector backfill embeds docs missing
        /// vectors. `force` governs only *re-embedding unchanged files* — the
        /// expensive part, and pure waste unless the embedding model changed.
        /// The app's embedder is fixed, so it never has. A `force: true` "full
        /// rebuild" would be the affordance for an embedder-dimension change;
        /// deliberately not built as a toggle today.
        var force: Bool { false }

        var isReindex: Bool {
            switch self {
            case .indexFolder: false
            case .reindexAll: true
            }
        }

        var verb: String { isReindex ? "Reindexing" : "Indexing" }
    }

    /// One root's finished contribution to the sequence.
    struct RootOutcome: Identifiable {
        enum Result {
            case completed
            /// Cancelled — stats are partial, and the sequence stopped here.
            case cancelled
            case failed(String)
        }

        let root: String
        let result: Result
        let stats: IndexStats

        var id: String { root }

        var displayName: String { URL(fileURLWithPath: root).lastPathComponent }
    }

    // MARK: - State

    /// The state of the root currently in flight. Overall position lives in
    /// `rootIndex` / `operation`, and finished roots in `completed`, so the
    /// terminal summary can always render what got done regardless of how the
    /// sequence ended.
    enum State {
        case idle
        /// POSTed, waiting on the 202.
        case starting
        /// Running. `phase` is the discriminator the progress bar keys off:
        /// `scan` (total unknown → indeterminate) vs `embed` (total known →
        /// determinate) vs `reconciling` (the stale sweep, usually a blink).
        case running(phase: String?, stats: IndexStats, done: Int, total: Int?)
        case done
        /// The user cancelled; the sequence stopped rather than advancing.
        case cancelled
        /// A root failed; the sequence stopped rather than skipping it.
        case failed(String)

        var isRunning: Bool {
            switch self {
            case .starting, .running: true
            case .idle, .done, .cancelled, .failed: false
            }
        }

        var isTerminal: Bool {
            switch self {
            case .done, .cancelled, .failed: true
            case .idle, .starting, .running: false
            }
        }
    }

    private(set) var state: State = .idle
    private(set) var operation: Operation?
    /// 0-based position of the root in flight, for "folder N of M".
    private(set) var rootIndex = 0
    private(set) var completed: [RootOutcome] = []

    /// Cancel is *requested*, not synchronously effected: the pipeline finishes
    /// its current file/batch and emits a terminal `cancelled` event. This flag
    /// is what puts the sheet in "Cancelling…" for that window.
    private(set) var isCancelling = false

    /// Sheet presentation. Settable so `ContentView` can bind to it.
    var isPresenting = false

    /// Set when Reindex was asked for but there is nothing to reindex, so the
    /// rail can say why instead of silently doing nothing.
    private(set) var noRootsMessage: String?

    /// Called after an operation finishes, once health has been re-fetched, so
    /// the search UI can re-derive its resting state (the empty-index
    /// onboarding prompt has to clear, and reindex can empty an index too).
    var onIndexCompleted: (() -> Void)?

    // MARK: - Derived

    var totalRoots: Int { operation?.roots.count ?? 0 }

    /// The root in flight, or the last one touched.
    var currentRoot: String? {
        guard let roots = operation?.roots, roots.indices.contains(rootIndex) else { return nil }
        return roots[rootIndex]
    }

    /// Whether to show the "folder N of M" line — a one-root reindex is still a
    /// sequence, but saying "1 of 1" is noise.
    var showsSequencePosition: Bool { totalRoots > 1 }

    /// Stats summed across every root that finished. Roots are recorded
    /// separately, so a sum is only meaningful when they don't overlap; nested
    /// roots (both `/a` and `/a/b` indexed) would double-count. The server
    /// records roots as given, so that's a real if unlikely case — the per-root
    /// rows in the summary are what stay exact.
    var aggregateStats: IndexStats { IndexStats.sum(completed.map(\.stats)) }

    // MARK: - Private

    private static let log = Logger(subsystem: "com.oasis.app", category: "index")

    private let controller: ServerController
    private let runner: IndexRunner
    /// The app's single `/api/status` reader — the roots Reindex needs and the
    /// statistics panel's data are the same read, so there is one of it.
    private let status: StatusViewModel
    private var sequenceTask: Task<Void, Never>?

    init(controller: ServerController, status: StatusViewModel) {
        self.controller = controller
        self.status = status
        self.runner = IndexRunner(controller: controller)
    }

    // MARK: - Entry points

    /// Folder picker → index. The chosen URL's path is the `root`.
    func chooseFolderAndIndex() {
        guard !state.isRunning else {
            Self.log.debug("index already running — ignoring a second request")
            isPresenting = true
            return
        }

        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.title = "Index a Folder"
        panel.message = "Choose a folder for Oasis to index."
        panel.prompt = "Index"
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser

        guard panel.runModal() == .OK, let folder = panel.urls.first else {
            Self.log.debug("folder picker cancelled")
            return
        }
        start(.indexFolder(folder.path))
    }

    /// Reindex every folder already indexed. **No picker** — the roots come from
    /// the server, which is the only thing that knows what this index covers.
    func reindexAll() {
        guard !state.isRunning else {
            isPresenting = true
            return
        }

        Task { @MainActor [weak self] in
            guard let self else { return }
            // Re-read rather than trusting the cached list: the roots the button
            // was enabled from could be minutes old.
            await self.status.refreshAndWait()
            let roots = self.status.indexedRoots
            guard !roots.isEmpty else {
                // Covers both "never indexed" and the legacy case: an index with
                // documents but no recorded roots (pre-root-tracking). The
                // honest answer is the same — we cannot know what to re-scan,
                // and guessing a root would aim the reconciliation sweep at a
                // tree it was never measured against.
                self.noRootsMessage = "No indexed folders yet — use Index New Folder."
                Self.log.notice("reindex requested with no recorded roots")
                return
            }
            self.noRootsMessage = nil
            Self.log.notice("reindexing \(roots.count) root(s)")
            self.start(.reindexAll(roots))
        }
    }


    private func start(_ operation: Operation) {
        guard !state.isRunning else { return }

        self.operation = operation
        rootIndex = 0
        completed = []
        isCancelling = false
        state = .starting
        isPresenting = true

        sequenceTask?.cancel()
        sequenceTask = Task { @MainActor [weak self] in
            await self?.runSequence(operation)
        }
    }

    /// Close the sheet after a terminal state. Resets so the next run is clean.
    func dismiss() {
        isPresenting = false
        guard state.isTerminal else { return }
        state = .idle
        isCancelling = false
        operation = nil
        rootIndex = 0
        completed = []
    }

    // MARK: - The sequence

    /// Run each root in turn. **Sequential by necessity** — the server holds a
    /// single-job lock and 409s a second POST — and stop-on-trouble by choice:
    /// a failed or cancelled root ends the operation instead of quietly moving
    /// on, so the summary can never imply a root was refreshed when it wasn't.
    private func runSequence(_ operation: Operation) async {
        for (position, root) in operation.roots.enumerated() {
            rootIndex = position
            state = .starting

            let outcome = await runner.run(root: root, force: operation.force) { [weak self] progress in
                guard let self, !Task.isCancelled else { return }
                self.state = .running(
                    phase: progress.phase,
                    stats: progress.stats,
                    done: progress.done,
                    total: progress.total
                )
            }

            switch outcome {
            case .done(let stats):
                completed.append(RootOutcome(root: root, result: .completed, stats: stats))

            case .cancelled(let stats):
                // Cancel stops the *operation*, not just this folder. Committed
                // work persists, so the partial stats are real and reported.
                completed.append(RootOutcome(root: root, result: .cancelled, stats: stats))
                isCancelling = false
                state = .cancelled
                await finish()
                return

            case .failed(let message):
                completed.append(RootOutcome(root: root, result: .failed(message), stats: .empty))
                isCancelling = false
                state = .failed(message)
                await finish()
                return

            case .interrupted:
                // Local teardown, not a server-side event: leave the UI alone
                // rather than reporting a failure that didn't happen.
                Self.log.debug("sequence interrupted locally at \(root, privacy: .public)")
                return
            }
        }

        state = .done
        await finish()
    }

    /// Everything that has to happen after the sequence settles, whichever way
    /// it settled.
    private func finish() async {
        // The server's `documents` just changed — by additions, and for reindex
        // also by the reconciliation sweep's deletions. The app's held
        // HealthResponse is stale until this runs, so the count and the
        // empty-state would both keep describing the pre-index world.
        await controller.refreshHealth()
        // Re-read `/api/status` too: it drives the statistics panel *and* the
        // Reindex button's roots, both of which the operation just changed —
        // documents, size, last-indexed, stale count, and (for a new folder) the
        // root list itself.
        await status.refreshAndWait()
        onIndexCompleted?()
    }

    // MARK: - Cancel

    /// Request cancellation of the running job, and with it the whole sequence.
    ///
    /// The UI settles on the terminal `cancelled` *event*, not on this click:
    /// the pipeline finishes its current file/batch first and reports partial
    /// stats. `runSequence` is what declines to advance to the next root.
    func cancel() {
        guard state.isRunning, !isCancelling else { return }
        isCancelling = true

        Task { @MainActor [weak self] in
            guard let self else { return }
            switch await self.runner.requestCancel() {
            case .requested, .alreadyFinished:
                // Keep consuming either way — the terminal event settles the UI.
                break
            case .failed(let message):
                Self.log.error("cancel failed: \(message, privacy: .public)")
                self.isCancelling = false
            }
        }
    }

}
