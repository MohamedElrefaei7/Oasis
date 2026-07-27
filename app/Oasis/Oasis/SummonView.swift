//
//  SummonView.swift
//  Oasis
//
//  The contents of the summon panel: a query line, and — only while the server
//  is still coming up — one line saying so.
//
//  It renders no results by design. The panel's job ends at Enter; the main
//  window shows what came back.
//

import SwiftUI

struct SummonView: View {

    /// Read for one thing only: whether a query typed right now can run
    /// immediately or has to be held. Step 1's states, unchanged.
    let controller: ServerController

    let onSubmit: (String) -> Void
    let onCancel: () -> Void
    /// Fires when the status row appears or disappears, so the panel can
    /// resize — SwiftUI can't resize an `NSPanel` on its own.
    let onLayoutChange: () -> Void

    @State private var query: String = ""
    @FocusState private var focused: Bool

    private static let queryRowHeight: CGFloat = 60
    private static let statusRowHeight: CGFloat = 32
    private static let cornerRadius: CGFloat = 14

    private var isReady: Bool {
        if case .ready = controller.state { return true }
        return false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            queryRow

            if !isReady {
                Divider()
                statusRow
            }
        }
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous)
                .strokeBorder(.separator, lineWidth: 1)
        )
        // The field must be first responder the instant the panel appears —
        // a summon you have to click into is a failed summon. `onAppear` fires
        // as the hosting view enters the window; the delayed second assignment
        // covers the ordering where the panel takes key *after* that, in which
        // case the first assignment lands on a window that can't yet hold focus.
        .onAppear { focused = true }
        .task {
            focused = true
            try? await Task.sleep(for: .milliseconds(40))
            focused = true
        }
        .onChange(of: isReady) { onLayoutChange() }
        // Redundant with the panel's key-down monitor, and kept: if SwiftUI
        // ever does see the Escape first, it should mean the same thing.
        .onExitCommand { onCancel() }
    }

    private var queryRow: some View {
        HStack(spacing: 12) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(.secondary)

            TextField("Search your files…", text: $query)
                .textFieldStyle(.plain)
                .font(.system(size: 21, weight: .regular))
                .focused($focused)
                // Enter hands off and dismisses. Same rule as the main window:
                // not search-as-you-type, because every search is a real round
                // trip through torch inference.
                .onSubmit { onSubmit(query) }
        }
        .padding(.horizontal, 18)
        .frame(height: Self.queryRowHeight)
    }

    /// The not-ready case, stated rather than hidden.
    ///
    /// The alternative — refusing the hotkey until the server is up — produces
    /// exactly the dead panel this is here to avoid: ⌘⌥O does nothing for the
    /// first half-minute after launch and the user concludes the hotkey is
    /// broken. Typing is allowed; Enter holds the query and the main window
    /// shows the warming screen it's waiting behind.
    @ViewBuilder
    private var statusRow: some View {
        HStack(spacing: 8) {
            switch controller.state {
            case .failed(let message):
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                Text("Oasis couldn't start — \(message)")
                    .lineLimit(1)
                    .truncationMode(.tail)

            default:
                ProgressView()
                    .controlSize(.small)
                    .scaleEffect(0.7)
                Text("Oasis is starting… press Return and your search will run as soon as it's ready.")
            }
        }
        .font(.callout)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 18)
        .frame(height: Self.statusRowHeight)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
