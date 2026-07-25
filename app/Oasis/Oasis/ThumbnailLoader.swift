//
//  ThumbnailLoader.swift
//  Oasis
//
//  QuickLook thumbnails for result cards, with a guaranteed fallback.
//  QuickLook and QuickLookThumbnailing are system frameworks — they auto-link
//  on import, so this needs no project-setting change.
//

import AppKit
import Foundation
import OSLog
import QuickLookThumbnailing

@MainActor
final class ThumbnailLoader {
    static let shared = ThumbnailLoader()

    private static let log = Logger(subsystem: "com.oasis.app", category: "thumbnails")

    /// Keyed by path only: every card requests the same size, so size isn't
    /// part of the identity. Revisit if cards ever become resizable.
    private var cache: [String: NSImage] = [:]
    /// De-duplicates concurrent requests for the same file — two cards (or a
    /// re-render mid-flight) await one generation instead of racing two.
    private var inFlight: [String: Task<NSImage, Never>] = [:]

    private init() {}

    func thumbnail(for path: String, size: CGSize, scale: CGFloat) async -> NSImage {
        if let cached = cache[path] { return cached }
        if let running = inFlight[path] { return await running.value }

        let task = Task { @MainActor () -> NSImage in
            await Self.generate(path: path, size: size, scale: scale)
        }
        inFlight[path] = task
        let image = await task.value
        inFlight[path] = nil
        cache[path] = image
        return image
    }

    private static func generate(path: String, size: CGSize, scale: CGFloat) async -> NSImage {
        let url = URL(fileURLWithPath: path)
        let request = QLThumbnailGenerator.Request(
            fileAt: url,
            size: size,
            scale: scale,
            representationTypes: .all
        )

        do {
            let representation = try await QLThumbnailGenerator.shared
                .generateBestRepresentation(for: request)
            return representation.nsImage
        } catch {
            // Expected, not exceptional: unsupported file types have no
            // QuickLook preview, and a file indexed earlier may since have been
            // deleted from disk. Never render an empty box — fall back to the
            // system's file-type icon, which is always available.
            Self.log.debug("no QuickLook thumbnail for \(url.lastPathComponent, privacy: .public): \(error.localizedDescription, privacy: .public)")
            let icon = NSWorkspace.shared.icon(forFile: path)
            icon.size = size
            return icon
        }
    }
}
