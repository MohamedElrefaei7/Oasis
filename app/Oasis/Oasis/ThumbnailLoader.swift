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

    /// **`NSCache`, not a plain dictionary — Oasis is resident, not a session.**
    ///
    /// The app stays alive in the menu bar indefinitely (that is what makes the
    /// global summon global), so a dictionary here grows for the life of the
    /// *login session*: every result of every search, one decoded `NSImage`
    /// each, never evicted. `NSCache` bounds it and, unlike a hand-rolled LRU,
    /// also drops its contents under system memory pressure.
    ///
    /// Keyed by path only: every card requests the same size, so size isn't
    /// part of the identity. Revisit if cards ever become resizable.
    private let cache: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        // Generous next to a result grid (2 columns × a scrolling page) and far
        // short of unbounded. Counted in entries, not bytes: these are all the
        // same requested size, so entries are a fair proxy.
        cache.countLimit = 512
        return cache
    }()

    /// De-duplicates concurrent requests for the same file — two cards (or a
    /// re-render mid-flight) await one generation instead of racing two.
    private var inFlight: [String: Task<NSImage, Never>] = [:]

    private init() {}

    func thumbnail(for path: String, size: CGSize, scale: CGFloat) async -> NSImage {
        if let cached = cache.object(forKey: path as NSString) { return cached }
        if let running = inFlight[path] { return await running.value }

        let task = Task { @MainActor () -> NSImage in
            await Self.generate(path: path, size: size, scale: scale)
        }
        inFlight[path] = task
        let image = await task.value
        inFlight[path] = nil
        cache.setObject(image, forKey: path as NSString)
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
