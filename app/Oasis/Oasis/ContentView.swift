//
//  ContentView.swift
//  Oasis
//
//  Three states over the server lifecycle: warming, ready, failed. No search,
//  no actions — this view exists so a human watching the window can tell
//  exactly which state the seam is in and why.
//

import SwiftUI

struct ContentView: View {
    let controller: ServerController

    var body: some View {
        VStack(spacing: 20) {
            switch controller.state {
            case .idle:
                stateBlock(title: "Starting Oasis…") {
                    ProgressView().controlSize(.small)
                }

            case .starting:
                stateBlock(title: "Starting the Oasis server…") {
                    ProgressView().controlSize(.small)
                    Text("Waiting for the handshake.")
                        .foregroundStyle(.secondary)
                }

            case .warming(let since):
                warming(since: since)

            case .ready(let health):
                ready(health)

            case .failed(let message):
                failed(message)
            }
        }
        .padding(36)
        .frame(minWidth: 440, minHeight: 260)
    }

    // MARK: - States

    /// The long one: measured at 35–54 s (docs/APP_SEAM.md §4). The live
    /// elapsed counter makes every launch a re-measurement of that window — if
    /// it routinely runs past what the doc claims, the doc is what's wrong.
    private func warming(since: Date) -> some View {
        stateBlock(title: "Warming up…") {
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

    /// Renders real fields off the health payload, not just "a 200 came back" —
    /// that is what proves the round trip decoded and reflects server truth.
    private func ready(_ health: HealthResponse) -> some View {
        stateBlock(title: "Oasis is ready") {
            Image(systemName: "checkmark.circle.fill")
                .imageScale(.large)
                .foregroundStyle(.green)

            Text(health.indexSummary)
                .font(.headline)

            if health.reindexRecommended == true {
                Label("Reindex recommended", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
            } else {
                Text("reindex_recommended: false")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }

            if let version = health.version {
                Text("server \(version)")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    private func failed(_ message: String) -> some View {
        stateBlock(title: "Oasis couldn't start") {
            Image(systemName: "xmark.octagon.fill")
                .imageScale(.large)
                .foregroundStyle(.red)

            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .textSelection(.enabled)
                .frame(maxWidth: 420)

            // Full teardown + respawn, not a resume: the child is terminated
            // (if it's still alive) and the whole sequence runs from scratch.
            Button("Retry") { controller.retry() }
                .keyboardShortcut(.defaultAction)
                .padding(.top, 4)
        }
    }

    // MARK: -

    private func stateBlock<Content: View>(
        title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(spacing: 12) {
            Text(title)
                .font(.title2)
            content()
        }
    }
}

#Preview {
    // Not started — the preview renders `.idle`; a preview must never spawn a
    // server.
    ContentView(controller: ServerController())
}
