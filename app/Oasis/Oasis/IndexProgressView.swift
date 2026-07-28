//
//  IndexProgressView.swift
//  Oasis
//
//  The index progress sheet, shared by Index New Folder (one root) and Reindex
//  Current Folders (N roots). Nothing here decides anything — it renders
//  whatever the latest event put in `IndexViewModel`.
//

import SwiftUI

struct IndexProgressView: View {
    @Bindable var viewModel: IndexViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header

            switch viewModel.state {
            case .idle, .starting:
                startingBody

            case .running(let phase, let stats, let done, let total):
                runningBody(phase: phase, stats: stats, done: done, total: total)

            case .done:
                terminalBody(
                    title: viewModel.showsSequencePosition
                        ? "Reindexed \(viewModel.completed.count) folders"
                        : "Indexing complete",
                    systemImage: "checkmark.circle.fill",
                    tint: .green
                )

            case .cancelled:
                terminalBody(
                    title: cancelledTitle,
                    systemImage: "stop.circle.fill",
                    tint: .orange,
                    // The partial-stats point, said out loud: nothing is undone.
                    note: viewModel.showsSequencePosition
                        ? "Cancelling stopped the whole operation — remaining folders were not touched. Work already finished was kept."
                        : "Work already finished was kept — indexing is incremental, so the next run picks up the rest."
                )

            case .failed(let message):
                failedBody(message)
            }

            Spacer(minLength: 0)
            footer
        }
        .padding(24)
        .frame(width: 480, height: 360)
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.title3.weight(.semibold))

            // Overall position, above the per-root bar. Only for a real
            // sequence — "1 of 1" is noise.
            if viewModel.showsSequencePosition, viewModel.state.isRunning {
                Text("Folder \(viewModel.rootIndex + 1) of \(viewModel.totalRoots)")
                    .font(.callout.weight(.medium))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }

            if let root = currentRootPath {
                Text(root)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(root)
            }
        }
    }

    /// The path to show under the title: the root in flight while running, and
    /// nothing once finished (the summary lists every root by then).
    private var currentRootPath: String? {
        guard !viewModel.state.isTerminal || !viewModel.showsSequencePosition else { return nil }
        return viewModel.currentRoot
    }

    /// The header already says "Cancelled"; this line says *where* it stopped,
    /// which is the part a multi-root run leaves ambiguous.
    private var cancelledTitle: String {
        guard viewModel.showsSequencePosition else { return "Indexing cancelled" }
        let stoppedAt = viewModel.completed.last?.displayName ?? "the current folder"
        return "Stopped during \(stoppedAt) — folder \(viewModel.completed.count) of \(viewModel.totalRoots)"
    }

    private var title: String {
        let verb = viewModel.operation?.verb ?? "Indexing"
        switch viewModel.state {
        case .idle, .starting: return "Starting…"
        case .running: return viewModel.isCancelling ? "Cancelling…" : verb
        case .done: return "Done"
        case .cancelled: return "Cancelled"
        case .failed: return "\(verb) failed"
        }
    }

    // MARK: - Bodies

    private var startingBody: some View {
        VStack(alignment: .leading, spacing: 10) {
            ProgressView()
                .progressViewStyle(.linear)
            Text("Asking the server to start the job…")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }

    /// **`phase` drives the bar, not `total == nil`.** The scan phase reports a
    /// null total because the walk is a lazy generator (the count genuinely
    /// isn't known yet) → indeterminate. The embed phase reports a real total →
    /// determinate. Keying off "total is null" would render "done but empty" the
    /// same as "still walking", which is exactly what the discriminator exists
    /// to prevent.
    @ViewBuilder
    private func runningBody(phase: String?, stats: IndexStats, done: Int, total: Int?) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if phase == "embed", let total, total > 0 {
                ProgressView(value: Double(min(done, total)), total: Double(total))
                    .progressViewStyle(.linear)
                Text("Embedding — \(done.formatted()) / \(total.formatted()) chunks")
                    .font(.callout)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            } else {
                ProgressView()
                    .progressViewStyle(.linear)
                Text(indeterminateLabel(phase: phase, done: done))
                    .font(.callout)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }

            if viewModel.isCancelling {
                Text(
                    viewModel.showsSequencePosition
                        ? "Finishing the current file, then stopping the whole operation."
                        : "Finishing the current file, then stopping."
                )
                .font(.caption)
                .foregroundStyle(.tertiary)
            }

            Divider().padding(.vertical, 2)
            liveCounts(stats)

            // Folders already finished in this sequence, so progress through a
            // multi-root reindex is legible while it runs.
            if !viewModel.completed.isEmpty {
                Text(finishedSoFarLine)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    private var finishedSoFarLine: String {
        let aggregate = viewModel.aggregateStats
        let folders = viewModel.completed.count
        return "\(folders) folder\(folders == 1 ? "" : "s") done — \(aggregate.indexed.formatted()) indexed, \(aggregate.removed.formatted()) removed"
    }

    private func indeterminateLabel(phase: String?, done: Int) -> String {
        switch phase {
        case "reconciling": "Removing files that are no longer on disk…"
        case "embed": "Embedding…"
        default: "Scanning… \(done.formatted()) file\(done == 1 ? "" : "s")"
        }
    }

    private func liveCounts(_ stats: IndexStats) -> some View {
        HStack(spacing: 18) {
            count("Indexed", stats.indexed)
            count("Skipped", stats.skipped)
            count("Chunks", stats.chunks)
            Spacer(minLength: 0)
        }
        .font(.caption)
    }

    private func count(_ label: String, _ value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value.formatted())
                .monospacedDigit()
                .font(.callout.weight(.medium))
            Text(label)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Terminal summary

    @ViewBuilder
    private func terminalBody(
        title: String,
        systemImage: String,
        tint: Color,
        note: String? = nil
    ) -> some View {
        let stats = viewModel.aggregateStats

        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(.callout.weight(.medium))
                .foregroundStyle(tint)

            summaryGrid(stats)

            // Per-root breakdown: an aggregate alone can't say *which* folder
            // was cancelled or which one contributed the removals.
            if viewModel.showsSequencePosition {
                perRootRows
            }

            permissionHint(stats)

            if let note {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func summaryGrid(_ stats: IndexStats) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
            summaryRow("Files indexed", stats.indexed)
            summaryRow("Unchanged, skipped", stats.skipped)
            summaryRow("Chunks embedded", stats.chunks)
            // Reconciliation made visible. This is the first place the stale
            // sweep surfaces to the user, and on a reindex it is the whole
            // point — so it shows whenever the operation could have swept,
            // including at zero.
            if stats.removed > 0 || viewModel.operation?.isReindex == true {
                GridRow {
                    Text("Removed (no longer on disk)")
                        .foregroundStyle(.secondary)
                    Text(stats.removed.formatted())
                        .monospacedDigit()
                        .foregroundStyle(stats.removed > 0 ? .primary : .secondary)
                }
            }
            if stats.unsupported > 0 {
                summaryRow("Unsupported file types", stats.unsupported)
            }
            if stats.failed > 0 {
                summaryRow("Failed to read", stats.failed)
            }
        }
        .font(.callout)
    }

    private var perRootRows: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(viewModel.completed) { outcome in
                    HStack(spacing: 8) {
                        Image(systemName: symbol(for: outcome.result))
                            .foregroundStyle(tint(for: outcome.result))
                            .font(.caption)
                        Text(outcome.displayName)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .help(outcome.root)
                        Spacer(minLength: 8)
                        Text(rootDetail(outcome))
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                    .font(.caption)
                }

                // Roots the sequence never reached, so "stopped" is explicit
                // rather than inferred from a short list.
                if let skipped = untouchedRootCount, skipped > 0 {
                    Text("\(skipped) folder\(skipped == 1 ? "" : "s") not reached")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxHeight: 90)
    }

    private var untouchedRootCount: Int? {
        guard viewModel.state.isTerminal else { return nil }
        return max(0, viewModel.totalRoots - viewModel.completed.count)
    }

    private func rootDetail(_ outcome: IndexViewModel.RootOutcome) -> String {
        switch outcome.result {
        case .completed, .cancelled:
            var parts = ["\(outcome.stats.indexed.formatted()) indexed"]
            if outcome.stats.removed > 0 { parts.append("\(outcome.stats.removed.formatted()) removed") }
            return parts.joined(separator: ", ")
        case .failed:
            return "failed"
        }
    }

    private func symbol(for result: IndexViewModel.RootOutcome.Result) -> String {
        switch result {
        case .completed: "checkmark.circle.fill"
        case .cancelled: "stop.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        }
    }

    private func tint(for result: IndexViewModel.RootOutcome.Result) -> Color {
        switch result {
        case .completed: .green
        case .cancelled: .orange
        case .failed: .red
        }
    }

    /// The one stat that means "Oasis needs permission", not "Oasis is broken" —
    /// the whole reason it's counted separately from `failed`. The full Full
    /// Disk Access onboarding is its own step; this surfaces the signal so the
    /// number isn't silent.
    @ViewBuilder
    private func permissionHint(_ stats: IndexStats) -> some View {
        if stats.permissionDenied > 0 {
            Label {
                Text("\(stats.permissionDenied.formatted()) file\(stats.permissionDenied == 1 ? "" : "s") skipped — grant Full Disk Access in System Settings ▸ Privacy & Security to index protected folders.")
            } icon: {
                Image(systemName: "lock.fill")
            }
            .font(.caption)
            .foregroundStyle(.orange)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func summaryRow(_ label: String, _ value: Int) -> some View {
        GridRow {
            Text(label)
                .foregroundStyle(.secondary)
            Text(value.formatted())
                .monospacedDigit()
        }
    }

    private func failedBody(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(
                viewModel.showsSequencePosition
                    ? "Stopped at folder \(viewModel.rootIndex + 1) of \(viewModel.totalRoots)"
                    : "Indexing failed",
                systemImage: "exclamationmark.triangle.fill"
            )
            .font(.callout.weight(.medium))
            .foregroundStyle(.orange)

            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)

            // The wedge, named at the moment it bites. Reindex is
            // stop-and-report, so a root deleted from disk 400s and halts the
            // refresh of every *other* folder — and the message the server
            // sends ("Not a directory: …") describes the cause without hinting
            // at the fix. Until step 8 there was no fix to hint at; now there
            // is one, and this is the only screen where the user meets the
            // problem.
            if let root = viewModel.currentRoot,
               !FileManager.default.fileExists(atPath: root) {
                Label(
                    "This folder no longer exists on disk. Remove it in Settings ▸ Folders (⌘,) to unblock Reindex.",
                    systemImage: "folder.badge.questionmark"
                )
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }

            // A failed sequence still did real work on earlier roots; showing it
            // is the difference between "nothing happened" and "two of three
            // folders were refreshed".
            if !viewModel.completed.isEmpty {
                Divider()
                summaryGrid(viewModel.aggregateStats)
                if viewModel.showsSequencePosition { perRootRows }
            }
        }
    }

    // MARK: - Footer

    @ViewBuilder
    private var footer: some View {
        HStack {
            Spacer()
            if viewModel.state.isRunning {
                Button("Cancel") { viewModel.cancel() }
                    .disabled(viewModel.isCancelling)
            } else {
                Button("Done") { viewModel.dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
        }
    }
}
