//
//  ContentView.swift
//  Oasis
//
//  The main window: a lifecycle gate over step 1's server states, and — once
//  ready — the query bar, the result grid, and the (inert) control rail.
//

import SwiftUI

struct ContentView: View {
    /// App-level, not window-level (step 7): it owns the search state the
    /// summon panel writes into, and it outlives this window.
    let coordinator: AppSearchCoordinator

    @Environment(\.openWindow) private var openWindow

    @State private var indexViewModel: IndexViewModel
    @State private var statusViewModel: StatusViewModel
    /// Window-scoped, unlike the search state: opening a file is something you
    /// do *to* a result on screen, so it has nothing to outlive the window for.
    @State private var opener: DocumentOpener
    @FocusState private var queryFocused: Bool

    private var controller: ServerController { coordinator.controller }
    /// The one `SearchViewModel` in the process. Deliberately **not** `@State`:
    /// window-scoped search state would be recreated (and the panel's query
    /// lost) every time the window is closed and reopened.
    private var viewModel: SearchViewModel { coordinator.search }

    init(coordinator: AppSearchCoordinator) {
        self.coordinator = coordinator
        // One `/api/status` reader, shared: it feeds the statistics panel and
        // the roots Reindex re-scans, and those must never disagree. Both stay
        // window-scoped — neither is touched by the summon hand-off.
        let status = StatusViewModel(controller: coordinator.controller)
        _statusViewModel = State(initialValue: status)
        _indexViewModel = State(initialValue: IndexViewModel(controller: coordinator.controller, status: status))
        _opener = State(initialValue: DocumentOpener(controller: coordinator.controller))
    }

    var body: some View {
        Group {
            switch controller.state {
            case .idle:
                lifecycleBlock(title: "Starting Oasis…") {
                    ProgressView().controlSize(.small)
                }

            case .starting:
                lifecycleBlock(title: "Starting the Oasis server…") {
                    ProgressView().controlSize(.small)
                    Text("Waiting for the handshake.")
                        .foregroundStyle(.secondary)
                }

            case .warming(let since):
                warming(since: since)

            case .failed(let message):
                failed(message)

            // The search UI exists only here. You cannot search a server that
            // isn't up, so the query field is unreachable — not merely
            // disabled — until the server reports ready.
            case .ready(let health):
                mainWindow(health)
            }
        }
        .frame(minWidth: 940, minHeight: 620)
    }

    // MARK: - Main window

    private func mainWindow(_ health: HealthResponse) -> some View {
        HStack(alignment: .top, spacing: 20) {
            VStack(alignment: .leading, spacing: 16) {
                queryBar
                resultArea
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            }

            ControlRail(
                status: statusViewModel,
                canReindex: !statusViewModel.indexedRoots.isEmpty,
                indexedRootCount: statusViewModel.indexedRoots.count,
                noRootsMessage: indexViewModel.noRootsMessage,
                isIndexing: indexViewModel.state.isRunning || indexViewModel.isResetting,
                resetMessage: indexViewModel.resetMessage,
                onIndexNewFolder: { indexViewModel.chooseFolderAndIndex() },
                onReindex: { indexViewModel.reindexAll() },
                onReset: { indexViewModel.reset() }
            )
            .frame(width: 260)
        }
        .padding(20)
        .onAppear {
            // The summon hand-off needs a way to reopen this window once it has
            // been closed, and `openWindow` is only readable from a view.
            // Registered here as well as on the menu-bar label because this is
            // the one that runs at launch.
            coordinator.registerOpenWindow(openWindow)
            // A ready app is immediately typable.
            queryFocused = true
            viewModel.refreshRestingState()
            // A query typed into the summon panel while the server was still
            // warming was held rather than refused; this is the first moment it
            // can actually run. No-op in the common case.
            coordinator.runPendingQueryIfNeeded()
            // Fills the statistics panel, and tells the Reindex button whether
            // there are roots to re-scan. `IndexViewModel` re-reads the same
            // model after every operation, so the panel never shows counts from
            // before the index the user just ran.
            statusViewModel.refresh()
            // A finished index changes `documents`, so the search area's resting
            // state has to be re-derived: the empty-index onboarding prompt must
            // clear once there's content to search. `IndexViewModel` calls this
            // *after* it has re-fetched health, so the count it reads is fresh.
            indexViewModel.onIndexCompleted = {
                viewModel.indexDidChange()
            }
        }
        .sheet(isPresented: $indexViewModel.isPresenting) {
            IndexProgressView(viewModel: indexViewModel)
        }
    }

    private var queryBar: some View {
        // `@Bindable` rather than `$viewModel`: the model is app-level now, so
        // there is no `@State` projection to bind through — this is how you get
        // a binding into an `@Observable` the view doesn't own.
        @Bindable var viewModel = coordinator.search

        return HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)

            TextField("Search your files…", text: $viewModel.query)
                .textFieldStyle(.plain)
                .font(.title3)
                .focused($queryFocused)
                // Enter to submit, deliberately not search-as-you-type: every
                // search is a real round trip through torch inference. Kept as
                // a backstop below `onKeyPress(.return)`, which handles the
                // same key and only reaches `submit()` when nothing is
                // highlighted.
                .onSubmit { viewModel.submit() }
                // Keyboard navigation of the results, driven from the text
                // field rather than by moving focus into the grid.
                //
                // **Focus never leaves the query line** — the same model
                // Spotlight and every browser's address bar use, and the reason
                // is that the two things a user does here interleave: refine
                // the query, look at the answers, refine again. Moving real
                // focus into the grid would mean typing the next query requires
                // getting focus *back*, and the caret would have to be
                // re-established every time.
                //
                // These handlers attach to the focused field, so they see the
                // key before the field editor does; returning `.ignored` hands
                // it back, which is how ← → still move the caret when no result
                // is highlighted.
                .onKeyPress(.upArrow) { move(.up) }
                .onKeyPress(.downArrow) { move(.down) }
                .onKeyPress(.leftArrow) { move(.left) }
                .onKeyPress(.rightArrow) { move(.right) }
                .onKeyPress(.return) {
                    // The one genuinely ambiguous key: Return means "open the
                    // highlighted result" when there is one and "run this
                    // search" when there isn't. Resolved here rather than left
                    // to `onSubmit`, so exactly one of the two ever fires.
                    if let selected = viewModel.selectedResult {
                        opener.open(selected)
                    } else {
                        viewModel.submit()
                    }
                    return .handled
                }
                .onKeyPress(.escape) {
                    // Escape drops the highlight and leaves the query alone. If
                    // nothing is highlighted it isn't ours — the user is
                    // probably reaching for a sheet or the window.
                    viewModel.clearSelection() ? .handled : .ignored
                }

            if !viewModel.query.isEmpty {
                Button {
                    viewModel.query = ""
                    viewModel.submit()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 10))
    }

    /// Arrow-key handler shared by the four directions.
    ///
    /// `.ignored` when the grid didn't take the key, so an arrow with no
    /// results (or ↑ from the top row) still moves the caret in the field.
    private func move(_ direction: SearchViewModel.MoveDirection) -> KeyPress.Result {
        viewModel.moveSelection(direction, columns: Self.columns.count) ? .handled : .ignored
    }

    // MARK: - Result area

    /// Row-major fill *is* the required ranking — left to right, top to bottom.
    /// Feeding `LazyVGrid` the server's array in order satisfies it for free,
    /// so nothing here sorts or regroups: the server's order is the rank.
    private static let columns = [
        GridItem(.flexible(), spacing: 16),
        GridItem(.flexible(), spacing: 16),
    ]

    @ViewBuilder
    private var resultArea: some View {
        switch viewModel.state {
        case .idle:
            // Per the sketch: blank until asked. A faint hint, nothing more.
            centered {
                Text("Type a query to search your files.")
                    .foregroundStyle(.tertiary)
            }

        case .searching:
            centered {
                ProgressView().controlSize(.small)
                Text("Searching…").foregroundStyle(.secondary)
            }

        case .results(let results):
            VStack(spacing: 12) {
                if let failure = opener.failure {
                    openFailureBanner(failure)
                }

                // `ScrollViewReader` so the keyboard can reach results that are
                // scrolled out of view — without it, ↓ past the fold moves an
                // invisible highlight and the grid looks frozen.
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVGrid(columns: Self.columns, spacing: 16) {
                            ForEach(Array(results.enumerated()), id: \.element.id) { index, result in
                                ResultCard(
                                    result: result,
                                    isOpening: opener.isOpening(result),
                                    isSelected: viewModel.selectedIndex == index,
                                    onOpen: {
                                        // Clicking moves the highlight too, so
                                        // the keyboard carries on from wherever
                                        // the mouse left it rather than jumping
                                        // back to the top.
                                        viewModel.select(index)
                                        opener.open(result)
                                    }
                                )
                                .id(result.id)
                            }
                        }
                    }
                    .onChange(of: viewModel.selectedIndex) { _, index in
                        guard let index, results.indices.contains(index) else { return }
                        withAnimation(.easeOut(duration: 0.15)) {
                            proxy.scrollTo(results[index].id, anchor: .center)
                        }
                    }
                }
            }

        case .noMatches(let query):
            centered {
                Image(systemName: "magnifyingglass")
                    .imageScale(.large)
                    .foregroundStyle(.tertiary)
                Text("No matches for “\(query)”")
                    .font(.headline)
                Text("Try different words, or index more folders.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

        // The distinct empty state APP_SEAM.md §6e exists to protect: an index
        // that is empty (never built, or reset) is onboarding, not a dead grid
        // and not an error.
        case .empty:
            centered {
                Image(systemName: "tray")
                    .imageScale(.large)
                    .foregroundStyle(.tertiary)
                Text("Nothing indexed yet")
                    .font(.headline)
                Text("Pick a folder and Oasis will index it — this is the same action as **Index New Folder** on the right.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 360)
                // The onboarding prompt hands off into the identical flow, so
                // the empty state is a way *out* of itself rather than an
                // instruction to go look elsewhere.
                Button("Index a Folder…") {
                    indexViewModel.chooseFolderAndIndex()
                }
                .controlSize(.large)
                .padding(.top, 4)
            }

        case .failed(let message):
            centered {
                Image(systemName: "exclamationmark.triangle.fill")
                    .imageScale(.large)
                    .foregroundStyle(.orange)
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .textSelection(.enabled)
                    .frame(maxWidth: 420)
            }
        }
    }

    /// A failed open is reported next to the results, not in a modal.
    ///
    /// An alert would demand a click to get back to a grid that is still
    /// perfectly usable — the other seven results still open fine. The one
    /// failure worth acting on (410: indexed, since moved) names the fix, and
    /// the banner stays until it's dismissed or the next open succeeds.
    private func openFailureBanner(_ failure: DocumentOpener.Failure) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)

            VStack(alignment: .leading, spacing: 2) {
                Text(failure.filename)
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(failure.message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            Button {
                opener.failure = nil
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Dismiss")
        }
        .padding(12)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Lifecycle states (step 1)

    private func warming(since: Date) -> some View {
        lifecycleBlock(title: "Warming up…") {
            ProgressView().controlSize(.small)
            TimelineView(.periodic(from: since, by: 1)) { context in
                Text("\(Int(context.date.timeIntervalSince(since)))s elapsed")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            // Deliberately a range with an upper bound, not an estimate: a cold
            // start is 35–55 s (APP_SEAM.md §4) while a warm one is under 10 s
            // (measured 7.3–8.7 s, 2026-07-25). Promising the warm number would
            // make every cold start look broken.
            Text("The server is loading its models — a few seconds when warm, up to a minute on a cold start.")
                .font(.callout)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
    }

    private func failed(_ message: String) -> some View {
        lifecycleBlock(title: "Oasis couldn't start") {
            Image(systemName: "xmark.octagon.fill")
                .imageScale(.large)
                .foregroundStyle(.red)

            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .textSelection(.enabled)
                .frame(maxWidth: 420)

            // Full teardown + respawn, not a resume.
            Button("Retry") { controller.retry() }
                .keyboardShortcut(.defaultAction)
                .padding(.top, 4)
        }
    }

    // MARK: -

    private func lifecycleBlock<Content: View>(
        title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(spacing: 12) {
            Text(title).font(.title2)
            content()
        }
        .padding(36)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func centered<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(spacing: 8) { content() }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Control rail

/// The sketch's right-hand rail: an action pair on top, the statistics panel in
/// the middle, a second pair at the bottom.
///
/// **Both index actions and the statistics panel are live; Reset and Settings
/// are still inert.** Those two are real, positioned, styled buttons so the
/// window is the true shape, but their actions are no-ops pending their own
/// steps.
private struct ControlRail: View {
    let status: StatusViewModel
    /// The index reports at least one root, so there is something to re-scan.
    let canReindex: Bool
    let indexedRootCount: Int
    /// Set when Reindex was asked for and there was nothing to do.
    let noRootsMessage: String?
    /// False while an index job runs (reset would 409) — see `canReset`.
    let isIndexing: Bool
    /// Set when a reset was refused.
    let resetMessage: String?
    let onIndexNewFolder: () -> Void
    let onReindex: () -> Void
    let onReset: () -> Void

    @State private var showingResetConfirm = false

    private var canReset: Bool { status.hasIndexOnDisk && !isIndexing }

    private var resetHelp: String {
        if isIndexing { return "An index is running — cancel or wait before resetting." }
        if !status.hasIndexOnDisk { return "There's no index to reset yet." }
        return "Permanently delete the index and all its search data."
    }

    /// Names what is destroyed and what it costs. `documentCount` is the live
    /// number from `/api/status`, so the dialog can't claim a stale figure.
    private var resetWarning: String {
        let documents = status.documentCount
        guard documents > 0 else {
            // Zero documents but an index on disk — clearing the recorded
            // folders is still a real, irreversible change.
            return "This clears the index and the list of indexed folders. This can't be undone."
        }
        return """
            This permanently removes all \(documents.formatted()) indexed \
            document\(documents == 1 ? "" : "s") and their search data. \
            You'll need to reindex your folders. This can't be undone.
            """
    }

    var body: some View {
        VStack(spacing: 16) {
            VStack(spacing: 8) {
                RailButton(title: "Index New Folder", systemImage: "folder.badge.plus", action: onIndexNewFolder)

                RailButton(title: "Reindex Current Folders", systemImage: "arrow.clockwise", action: onReindex)
                    // Nothing to reindex until the index records a root. A
                    // legacy index (documents but no recorded roots) lands here
                    // too, and correctly: we can't know what to re-scan.
                    .disabled(!canReindex)
                    .help(
                        canReindex
                            ? "Re-scan \(indexedRootCount) indexed folder\(indexedRootCount == 1 ? "" : "s") for new, changed and deleted files."
                            : "No indexed folders yet — use Index New Folder."
                    )

                if !canReindex || noRootsMessage != nil {
                    Text(noRootsMessage ?? "No indexed folders yet — use Index New Folder.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .multilineTextAlignment(.leading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }

            StatisticsPanelView(viewModel: status)

            Spacer(minLength: 0)

            VStack(spacing: 8) {
                RailButton(title: "Reset Indexing", systemImage: "trash", role: .destructive) {
                    showingResetConfirm = true
                }
                // Two independent reasons to refuse. Reset takes the same job
                // lock as indexing (the server would 409), and there is nothing
                // to destroy when no index file exists (it would 404) — so the
                // button is only live when it would actually succeed.
                .disabled(!canReset)
                .help(resetHelp)

                if let resetMessage {
                    Text(resetMessage)
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                RailButton(title: "Settings", systemImage: "gearshape") {
                    // TODO: later step — settings window.
                }
            }
            // A real confirmation that names the stakes. Reset is irreversible
            // and there is no undo, so the dialog says what goes and what it
            // costs — not a reflexive "Are you sure?". One clear destructive
            // confirm is enough; it matches the CLI's `--yes`.
            .confirmationDialog(
                "Reset the index?",
                isPresented: $showingResetConfirm,
                titleVisibility: .visible
            ) {
                Button("Reset Index", role: .destructive, action: onReset)
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(resetWarning)
            }
        }
    }

}

private struct RailButton: View {
    let title: String
    let systemImage: String
    var role: ButtonRole?
    let action: () -> Void

    var body: some View {
        Button(role: role, action: action) {
            Label(title, systemImage: systemImage)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(.bordered)
        .controlSize(.large)
    }
}

#Preview {
    ContentView(coordinator: AppSearchCoordinator(controller: ServerController()))
}
