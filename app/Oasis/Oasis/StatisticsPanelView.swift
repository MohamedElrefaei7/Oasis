//
//  StatisticsPanelView.swift
//  Oasis
//
//  The control rail's "Indexed File Statistics" panel — a read of `/api/status`,
//  replacing step 2's stub of em-dashes.
//
//  Two things here are more than a data dump, and they're the reason the
//  capability markers exist at all: `reindex_recommended` becomes a worded
//  nudge instead of a boolean, and `stale_documents` distinguishes "none" from
//  "not counted".
//

import SwiftUI

struct StatisticsPanelView: View {
    let viewModel: StatusViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Indexed File Statistics")
                .font(.headline)

            Divider()

            switch viewModel.state {
            case .loading:
                loading
            case .loaded(let status):
                populated(status)
            case .empty(let status):
                empty(status)
            case .failed(let message):
                failed(message)
            }

            Spacer(minLength: 0)
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 190, alignment: .topLeading)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: - States

    private var loading: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("Reading index…")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }

    /// No documents — calm, never an error. The index is new or was reset, and
    /// the panel's job here is to say so without looking broken.
    @ViewBuilder
    private func empty(_ status: StatusResponse?) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Nothing indexed yet", systemImage: "tray")
                .font(.callout.weight(.medium))
                .foregroundStyle(.secondary)

            Text("Index a folder and its statistics will appear here.")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)

            // A root with no documents is a real state — the pipeline records
            // the root before walking, so a folder holding nothing indexable
            // lands here. Showing it answers "which folder did I pick?".
            if let status, !status.indexedRoots.isEmpty {
                Divider()
                rootsSection(status.indexedRoots, caption: "Indexed, but no supported files were found.")
            }
        }
    }

    private func failed(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Couldn't read index status", systemImage: "exclamationmark.triangle.fill")
                .font(.callout.weight(.medium))
                .foregroundStyle(.orange)
            Text(message)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Populated

    @ViewBuilder
    private func populated(_ status: StatusResponse) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(spacing: 6) {
                statRow("Documents", value: status.documents.formatted())
                statRow("Index size", value: Self.formattedSize(status.dbSizeBytes))
                statRow("Last indexed", value: Self.relativeDate(status.lastIndexedAt), help: Self.absoluteDate(status.lastIndexedAt))
                statRow("Semantic search", value: status.semanticReady ? "Ready" : "Not ready", help: semanticDetail(status))
            }

            if status.reindexRecommended {
                reindexNudge(status)
            }

            staleLine(status)

            if !status.indexedRoots.isEmpty {
                Divider()
                rootsSection(status.indexedRoots, caption: nil)
            }

            // Small and muted: useful when something's wrong, noise otherwise.
            Text(status.dbPath)
                .font(.caption2)
                .foregroundStyle(.quaternary)
                .lineLimit(1)
                .truncationMode(.middle)
                .help(status.dbPath)
        }
    }

    // MARK: - The reindex nudge

    /// **The payoff of the capability markers.** `reindex_recommended` is
    /// derived server-side (the client does no version math), but the *granular*
    /// fields are kept alongside it precisely so the app can say which problem
    /// this is. An old keyword-only index otherwise just quietly under-serves
    /// semantic search, and the user's only evidence is that results feel worse
    /// than they should — the exact failure this panel exists to end.
    private func reindexNudge(_ status: StatusResponse) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Reindex recommended", systemImage: "exclamationmark.triangle.fill")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.orange)

            Text(reindexReason(status))
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            // **Point at an action that is actually available.** A legacy
            // pre-vector index is precisely the case this nudge exists for, and
            // it is also the case with no recorded roots — so Reindex is
            // *disabled* right above. Telling the user to press a greyed-out
            // button is worse than saying nothing; with no roots, re-adding the
            // folder is the real fix.
            Text(status.indexedRoots.isEmpty
                 ? "This index doesn't record which folders it covers, so use **Index New Folder** to add them again."
                 : "Use **Reindex Current Folders** above.")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
    }

    private func reindexReason(_ status: StatusResponse) -> String {
        if !status.vectorsBuilt {
            // The pre-vector index: keyword search works, semantic silently
            // doesn't. A plain reindex repairs it — the no-vector backfill means
            // this no longer needs a full rebuild.
            return "This index has no semantic-search data, so searches fall back to keywords only."
        }
        if !status.semanticReady {
            // Vectors exist but at the wrong width — unusable, and unusable in a
            // way that looks identical to "ready" without this field.
            let built = status.embeddingDimension.map(String.init) ?? "a different"
            return "Stored vectors were built for a different embedding model (\(built)-dimension), so semantic search can't use them."
        }
        return "This index was built by an older version of Oasis and should be rebuilt."
    }

    // MARK: - Stale

    /// **`nil` is "not computed", not "none".** Reporting an uncounted index as
    /// clean is a lie the user can't detect; saying the scan was skipped is
    /// honest and explains itself.
    @ViewBuilder
    private func staleLine(_ status: StatusResponse) -> some View {
        switch status.staleDocuments {
        case .none:
            Text("Stale count not computed (large index).")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        case .some(let stale) where stale > 0:
            Label {
                Text("\(stale.formatted()) file\(stale == 1 ? "" : "s") no longer on disk — reindex to clean up.")
            } icon: {
                Image(systemName: "clock.badge.exclamationmark")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        case .some:
            EmptyView()  // computed, none stale — nothing worth a line
        }
    }

    // MARK: - Roots

    /// Read-only **here** — removing a root lives in Settings ▸ Folders, on
    /// `POST /api/index/remove-root` (step 8).
    ///
    /// This panel is a summary, not a manager: it answers "what does my index
    /// cover?" beside the counts, and a destructive control has no business
    /// sitting in a statistics readout with no room for the confirm that has to
    /// precede it. The row-per-root layout this comment once reserved space for
    /// is what Settings ▸ Folders was built on, and both read the same
    /// `StatusViewModel`, so the two lists cannot disagree.
    private func rootsSection(_ roots: [String], caption: String?) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Folders")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Spacer()
                Text(roots.count.formatted())
                    .font(.callout)
                    .monospacedDigit()
            }

            ForEach(roots, id: \.self) { root in
                HStack(spacing: 6) {
                    Image(systemName: "folder")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Text(URL(fileURLWithPath: root).lastPathComponent)
                        .font(.caption)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 0)
                }
                .help(root)
            }

            if let caption {
                Text(caption)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - Rows and formatting

    private func statRow(_ label: String, value: String, help: String? = nil) -> some View {
        HStack {
            Text(label)
                .font(.callout)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .font(.callout)
                .monospacedDigit()
        }
        .help(help ?? "")
    }

    private func semanticDetail(_ status: StatusResponse) -> String {
        guard let model = status.embeddingModel else { return "No embedding model recorded." }
        let dimension = status.embeddingDimension.map { " (\($0)-dimension)" } ?? ""
        return "\(model)\(dimension)"
    }

    private static func formattedSize(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }

    private static let relativeFormatter: RelativeDateTimeFormatter = {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter
    }()

    private static func relativeDate(_ date: Date?) -> String {
        guard let date else { return "Never" }
        // An index that just finished lands within milliseconds of `now` — and
        // sometimes a hair *ahead* of it, since the server writes the timestamp
        // and we read it back across the same instant. `RelativeDateTimeFormatter`
        // renders that as "in 0 seconds", which reads as a bug in the panel the
        // moment after a successful index. Anything inside a minute is "Just now".
        let elapsed = Date().timeIntervalSince(date)
        if elapsed < 60 { return "Just now" }
        return relativeFormatter.localizedString(for: date, relativeTo: Date())
    }

    private static func absoluteDate(_ date: Date?) -> String {
        guard let date else { return "Never indexed" }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}
