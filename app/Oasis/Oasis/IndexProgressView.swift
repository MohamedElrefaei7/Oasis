//
//  IndexProgressView.swift
//  Oasis
//
//  The index progress sheet: phase label, progress bar, cancel, and the
//  terminal summary. Nothing here decides anything — it renders whatever the
//  latest event put in `IndexViewModel.state`.
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

            case .indexing(let phase, let stats, let done, let total):
                indexingBody(phase: phase, stats: stats, done: done, total: total)

            case .done(let stats):
                terminalBody(
                    title: "Indexing complete",
                    systemImage: "checkmark.circle.fill",
                    tint: .green,
                    stats: stats
                )

            case .cancelled(let stats):
                terminalBody(
                    title: "Indexing cancelled",
                    systemImage: "stop.circle.fill",
                    tint: .orange,
                    stats: stats,
                    // The partial-stats point, said out loud: nothing is undone.
                    note: "Work already finished was kept — indexing is incremental, so the next run picks up the rest."
                )

            case .failed(let message):
                failedBody(message)
            }

            Spacer(minLength: 0)
            footer
        }
        .padding(24)
        .frame(width: 460, height: 320)
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.title3.weight(.semibold))
            if let root = viewModel.root {
                Text(root.path)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(root.path)
            }
        }
    }

    private var title: String {
        switch viewModel.state {
        case .idle, .starting: "Starting…"
        case .indexing: viewModel.isCancelling ? "Cancelling…" : "Indexing"
        case .done: "Done"
        case .cancelled: "Cancelled"
        case .failed: "Indexing failed"
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
    private func indexingBody(phase: String?, stats: IndexStats, done: Int, total: Int?) -> some View {
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
                Text("Finishing the current file, then stopping.")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }

            Divider().padding(.vertical, 2)
            liveCounts(stats)
        }
    }

    private func indeterminateLabel(phase: String?, done: Int) -> String {
        switch phase {
        case "reconciling": "Cleaning up files that are no longer on disk…"
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

    @ViewBuilder
    private func terminalBody(
        title: String,
        systemImage: String,
        tint: Color,
        stats: IndexStats,
        note: String? = nil
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(.callout.weight(.medium))
                .foregroundStyle(tint)

            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                summaryRow("Files indexed", stats.indexed)
                summaryRow("Unchanged, skipped", stats.skipped)
                summaryRow("Chunks embedded", stats.chunks)
                if stats.removed > 0 {
                    summaryRow("Removed (gone from disk)", stats.removed)
                }
                if stats.unsupported > 0 {
                    summaryRow("Unsupported file types", stats.unsupported)
                }
                if stats.failed > 0 {
                    summaryRow("Failed to read", stats.failed)
                }
            }
            .font(.callout)

            // The one stat that means "Oasis needs permission", not "Oasis is
            // broken" — the whole reason it's counted separately from `failed`.
            // The full Full-Disk-Access onboarding is its own step; this just
            // surfaces the signal so the number isn't silent.
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

            if let note {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
            Label("Indexing failed", systemImage: "exclamationmark.triangle.fill")
                .font(.callout.weight(.medium))
                .foregroundStyle(.orange)
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Footer

    @ViewBuilder
    private var footer: some View {
        HStack {
            Spacer()
            if viewModel.state.isRunning {
                Button("Cancel") { viewModel.cancel() }
                    .disabled(viewModel.isCancelling || viewModel.jobID == nil)
            } else {
                Button("Done") { viewModel.dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
        }
    }
}
