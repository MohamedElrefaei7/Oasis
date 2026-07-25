//
//  ResultCard.swift
//  Oasis
//
//  One search result: thumbnail, title, highlighted snippet.
//

import SwiftUI

struct ResultCard: View {
    let result: SearchResult

    @Environment(\.displayScale) private var displayScale
    @State private var thumbnail: NSImage?

    private static let thumbnailSize = CGSize(width: 120, height: 120)

    var body: some View {
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
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
        .help(result.path)
        .task(id: result.path) {
            thumbnail = await ThumbnailLoader.shared.thumbnail(
                for: result.path,
                size: Self.thumbnailSize,
                scale: displayScale
            )
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
