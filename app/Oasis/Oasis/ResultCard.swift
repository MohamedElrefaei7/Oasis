//
//  ResultCard.swift
//  Oasis
//
//  One search result: thumbnail, title, highlighted snippet — and a click that
//  opens the file in whatever app owns it.
//

import SwiftUI

struct ResultCard: View {
    let result: SearchResult
    /// Whether this card's open request is in flight. Per-card, not per-grid.
    var isOpening: Bool = false
    /// The keyboard highlight. Distinct from hover, and stronger — hover says
    /// "you could click this", selection says "Return opens this".
    var isSelected: Bool = false
    var onOpen: () -> Void = {}

    @Environment(\.displayScale) private var displayScale
    @State private var thumbnail: NSImage?
    @State private var isHovering = false

    private static let thumbnailSize = CGSize(width: 120, height: 120)

    var body: some View {
        // A `Button`, not an `onTapGesture`. The gesture would look identical
        // and give up everything AppKit attaches to a real control: the card
        // becomes a single accessibility element with a "button" trait, VoiceOver
        // announces it, and Space activates it when focused.
        Button(action: onOpen) {
            card
        }
        .buttonStyle(.plain)
        // Single click, like Spotlight — a result list is a list of destinations,
        // not a file browser where selection and opening are separate acts.
        .disabled(isOpening)
        .onHover { isHovering = $0 }
        .accessibilityLabel("Open \(result.displayTitle)")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        // The path, not the title: two results can share a title, and when
        // something goes wrong the path is what tells you which file this is.
        .help(result.path)
    }

    private var card: some View {
        HStack(alignment: .top, spacing: 12) {
            thumbnailView

            VStack(alignment: .leading, spacing: 6) {
                Text(result.displayTitle)
                    .font(.headline)
                    .lineLimit(2)
                    .truncationMode(.middle)

                Text(Self.highlighted(result.snippet))
                    .font(.callout)
                    .lineLimit(4)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer(minLength: 0)
            }

            Spacer(minLength: 0)
        }
        .padding(12)
        .frame(height: 148, alignment: .top)
        .background(background, in: RoundedRectangle(cornerRadius: 10))
        // Nothing else in this window responds to the pointer, so without a
        // hover state a clickable card is indistinguishable from a static one —
        // the affordance is the only thing telling the user the click exists.
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(.tint.opacity(borderOpacity), lineWidth: isSelected ? 2.5 : 1.5)
        }
        .overlay(alignment: .topTrailing) { openingIndicator }
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .animation(.easeOut(duration: 0.12), value: isHovering)
        .animation(.easeOut(duration: 0.12), value: isSelected)
        .task(id: result.path) {
            thumbnail = await ThumbnailLoader.shared.thumbnail(
                for: result.path,
                size: Self.thumbnailSize,
                scale: displayScale
            )
        }
    }

    /// Selection outranks hover: the pointer can sit on one card while the
    /// keyboard is on another, and Return acts on the keyboard's.
    private var borderOpacity: Double {
        if isSelected { return 1 }
        return isHovering ? 0.55 : 0
    }

    private var background: some ShapeStyle {
        if isSelected { return AnyShapeStyle(.tint.opacity(0.14)) }
        return AnyShapeStyle(.quaternary.opacity(isHovering ? 0.9 : 0.5))
    }

    /// Launching an app is not instant, and a card that looks inert for a
    /// second invites the second click this spinner exists to make unnecessary.
    @ViewBuilder
    private var openingIndicator: some View {
        if isOpening {
            ProgressView()
                .controlSize(.small)
                .padding(10)
        }
    }

    @ViewBuilder
    private var thumbnailView: some View {
        Group {
            if let thumbnail {
                Image(nsImage: thumbnail)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
            } else {
                RoundedRectangle(cornerRadius: 6)
                    .fill(.quaternary)
                    .overlay { ProgressView().controlSize(.small) }
            }
        }
        .frame(width: 84, height: 84)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    /// Fold the segment list into an `AttributedString`, emphasizing matches.
    ///
    /// This is the whole point of the segment wire format: appending runs in
    /// order needs **no index arithmetic**, so the Python-codepoint /
    /// Swift-grapheme / UTF-16 mismatch that `{start, end}` offsets would have
    /// forced simply never arises. Emoji, combining marks and CJK in a snippet
    /// render correctly here for free.
    static func highlighted(_ segments: [Segment]) -> AttributedString {
        var output = AttributedString()
        for segment in segments {
            var run = AttributedString(segment.text)
            if segment.match {
                run.font = .callout.bold()
                run.foregroundColor = .primary
                run.backgroundColor = .yellow.opacity(0.35)
            } else {
                run.foregroundColor = .secondary
            }
            output.append(run)
        }
        return output
    }
}

#Preview {
    ResultCard(
        result: SearchResult(
            path: "/Users/you/Documents/q3-report.pdf",
            title: "Q3 Revenue Report",
            docId: 88,
            score: 0.0163,
            snippet: [
                Segment(text: "revenue", match: true),
                Segment(text: " grew 12% in Q3 driven by ", match: false),
                Segment(text: "enterprise renewals", match: true),
            ]
        )
    )
    .frame(width: 380)
    .padding()
}
