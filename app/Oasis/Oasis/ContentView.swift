//
//  ContentView.swift
//  Oasis
//
//  The main window: a lifecycle gate over step 1's server states, and — once
//  ready — the query bar, the result grid, and the (inert) control rail.
//

import SwiftUI

struct ContentView: View {
    let controller: ServerController

    @State private var viewModel: SearchViewModel
    @State private var indexViewModel: IndexViewModel
    @State private var statusViewModel: StatusViewModel
    @FocusState private var queryFocused: Bool

    init(controller: ServerController) {
        self.controller = controller
        // One `/api/status` reader, shared: it feeds the statistics panel and
        // the roots Reindex re-scans, and those must never disagree.
        let status = StatusViewModel(controller: controller)
        _statusViewModel = State(initialValue: status)
        _viewModel = State(initialValue: SearchViewModel(controller: controller))
        _indexViewModel = State(initialValue: IndexViewModel(controller: controller, status: status))
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
                onIndexNewFolder: { indexViewModel.chooseFolderAndIndex() },
                onReindex: { indexViewModel.reindexAll() }
            )
            .frame(width: 260)
        }
        .padding(20)
        .onAppear {
            // A ready app is immediately typable.
            queryFocused = true
            viewModel.refreshRestingState()
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
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)

            TextField("Search your files…", text: $viewModel.query)
                .textFieldStyle(.plain)
                .font(.title3)
                .focused($queryFocused)
                // Enter to submit, deliberately not search-as-you-type: every
                // search is a real round trip through torch inference.
                .onSubmit { viewModel.submit() }

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
            ScrollView {
                LazyVGrid(columns: Self.columns, spacing: 16) {
                    ForEach(results) { result in
                        ResultCard(result: result)
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
    let onIndexNewFolder: () -> Void
    let onReindex: () -> Void

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
                    // TODO: next step — confirm, then POST /api/reset {confirm: true}.
                }
                RailButton(title: "Settings", systemImage: "gearshape") {
                    // TODO: later step — settings window.
                }
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
    ContentView(controller: ServerController())
}
