//
//  SettingsView.swift
//  Oasis
//
//  The preferences window: General / Folders / Shortcuts / About.
//
//  Rendered inside SwiftUI's `Settings` scene, which is what buys the standard
//  macOS behaviour for free — the **⌘, menu item** under the app menu, one
//  window that reopens rather than duplicating, and the system's own titlebar
//  and tab chrome. Rolling a plain `Window` scene would mean reimplementing all
//  three and getting the menu item wrong.
//
//  **Scope.** Per-user preference and recourse only. Nothing here re-opens a
//  question the eval already answered — see the note at the top of
//  `SettingsModels.swift` for why search mode, reranking and NL parsing are
//  deliberately absent.
//

import AppKit
import KeyboardShortcuts
import SwiftUI

struct SettingsView: View {
    let coordinator: AppSearchCoordinator

    /// The Folders tab's client. Window-scoped state, but the *status* it reads
    /// through is app-level and shared with the main window (see
    /// `FoldersViewModel`).
    @State private var folders: FoldersViewModel
    @State private var launchAtLogin = LaunchAtLogin()

    init(coordinator: AppSearchCoordinator) {
        self.coordinator = coordinator
        _folders = State(initialValue: FoldersViewModel(coordinator: coordinator))
    }

    var body: some View {
        TabView {
            GeneralSettingsTab(
                status: coordinator.status,
                launchAtLogin: launchAtLogin
            )
            .tabItem { Label("General", systemImage: "gearshape") }

            FoldersSettingsTab(folders: folders, status: coordinator.status)
                .tabItem { Label("Folders", systemImage: "folder") }

            ShortcutsSettingsTab()
                .tabItem { Label("Shortcuts", systemImage: "keyboard") }

            AboutSettingsTab()
                .tabItem { Label("About", systemImage: "info.circle") }
        }
        // A fixed width keeps the four tabs from resizing the window as the user
        // moves between them; the height is a floor, since the folder list grows.
        .frame(width: 520)
        .frame(minHeight: 340)
        .onAppear { coordinator.status.refresh() }
    }
}

// MARK: - General

private struct GeneralSettingsTab: View {
    let status: StatusViewModel
    let launchAtLogin: LaunchAtLogin

    /// Bound with `@AppStorage` so the picker writes straight through to the
    /// same `UserDefaults` key `SearchViewModel` reads at request time. No
    /// observation plumbing between the two: the next search picks it up
    /// because it re-reads, which is also why a change applies without a
    /// restart.
    @AppStorage(PreferenceKey.resultLimit) private var resultLimit = ResultLimit.fallback

    var body: some View {
        Form {
            Section {
                Toggle(
                    "Launch Oasis at login",
                    isOn: Binding(
                        get: { launchAtLogin.isEnabled },
                        // Not a stored bool: the setter asks the system and the
                        // getter reads the system back, so a failed or
                        // system-revoked registration leaves the toggle showing
                        // the truth rather than the intent.
                        set: { launchAtLogin.setEnabled($0) }
                    )
                )
                Text("Oasis lives in the menu bar, so ⌘⌥O only works while it's running.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                if let note = launchAtLogin.message ?? launchAtLogin.statusDescription {
                    Text(note)
                        .font(.caption)
                        .foregroundStyle(launchAtLogin.message == nil ? Color.secondary : Color.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Section {
                Picker("Results shown:", selection: $resultLimit) {
                    ForEach(ResultLimit.presets, id: \.self) { count in
                        Text("\(count)").tag(count)
                    }
                }
                .pickerStyle(.segmented)
                Text("How many results each search asks for. The grid scrolls if they don't all fit.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                LabeledContent("Index file:") {
                    HStack(spacing: 8) {
                        Text(status.status?.dbPath ?? "No index yet")
                            .font(.callout)
                            .foregroundStyle(status.status == nil ? .secondary : .primary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .textSelection(.enabled)

                        Button("Show in Finder") { revealIndex() }
                            .disabled(status.status == nil)
                    }
                }
            }

            Section {
                LabeledContent("Full Disk Access:") {
                    VStack(alignment: .leading, spacing: 6) {
                        // Guidance, not the solution. Whether the *spawned
                        // server* inherits the app's TCC grant is a distribution
                        // question settled elsewhere; this is the here's-how
                        // helper, which is useful the moment anyone indexes a
                        // folder under a protected location (Desktop, Documents,
                        // Downloads, Mail) and harmless before that.
                        Text("macOS keeps some folders — Desktop, Documents, Downloads, Mail — behind a permission prompt. Without Full Disk Access, Oasis skips those files and reports them as skipped.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)

                        Button("Open Privacy Settings") { openFullDiskAccessSettings() }
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    /// Reveal — not open. `activateFileViewerSelecting` selects the file in a
    /// Finder window, where `NSWorkspace.open` on a `.db` would hand it to
    /// whatever app claims the extension.
    private func revealIndex() {
        guard let path = status.status?.dbPath else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }

    private func openFullDiskAccessSettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") else { return }
        NSWorkspace.shared.open(url)
    }
}

// MARK: - Folders

private struct FoldersSettingsTab: View {
    let folders: FoldersViewModel
    let status: StatusViewModel

    /// The row awaiting confirmation. Held as the root itself rather than a
    /// bool, so the dialog can name the folder — a destructive confirm that
    /// says "this folder" is not a confirm.
    @State private var pendingRemoval: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Folders Oasis has indexed")
                .font(.headline)

            if folders.roots.isEmpty {
                // Not an error. A never-indexed app and a fully-removed one land
                // here identically, and both are onboarding.
                VStack(alignment: .leading, spacing: 4) {
                    Text("No indexed folders yet.")
                        .foregroundStyle(.secondary)
                    Text("Use **Index New Folder** in the main window.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                List(folders.roots, id: \.self) { root in
                    FolderRow(
                        root: root,
                        isRemoving: folders.removing == root,
                        isDisabled: folders.removing != nil,
                        onRemove: { pendingRemoval = root }
                    )
                }
                .listStyle(.inset)
                .frame(minHeight: 150)
            }

            if let message = folders.message {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text("Removing a folder deletes its indexed documents from Oasis. The files themselves are never touched.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(20)
        .onAppear { folders.refresh() }
        // Scoped to one folder, unlike Reset's whole-index dialog — but the same
        // rule: name the stakes, and say the part users actually fear ("are my
        // files being deleted?") explicitly rather than leaving it to be
        // inferred from "indexed documents".
        .confirmationDialog(
            pendingRemoval.map { "Remove \(displayName(for: $0))?" } ?? "Remove folder?",
            isPresented: Binding(
                get: { pendingRemoval != nil },
                set: { if !$0 { pendingRemoval = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Remove Folder", role: .destructive) {
                if let root = pendingRemoval { folders.remove(root) }
                pendingRemoval = nil
            }
            Button("Cancel", role: .cancel) { pendingRemoval = nil }
        } message: {
            Text("Its indexed documents will be deleted from Oasis and will no longer appear in search results. The files on disk are untouched.")
        }
    }

    private func displayName(for root: String) -> String {
        URL(fileURLWithPath: root).lastPathComponent
    }
}

private struct FolderRow: View {
    let root: String
    let isRemoving: Bool
    let isDisabled: Bool
    let onRemove: () -> Void

    /// Whether the folder still exists on disk.
    ///
    /// **This is the wedge case, made visible.** A root deleted from disk is
    /// what jams Reindex — the sequence 400s on it and stops refreshing every
    /// other folder — so the row that is the recourse should say so, rather than
    /// leaving the user to work out why Reindex keeps failing.
    private var isMissing: Bool { !FileManager.default.fileExists(atPath: root) }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: isMissing ? "folder.badge.questionmark" : "folder")
                .foregroundStyle(isMissing ? .orange : .secondary)

            VStack(alignment: .leading, spacing: 2) {
                Text(URL(fileURLWithPath: root).lastPathComponent)
                    .lineLimit(1)
                Text(isMissing ? "Missing — this folder blocks Reindex until it's removed" : root)
                    .font(.caption)
                    .foregroundStyle(isMissing ? Color.orange : Color.secondary.opacity(0.7))
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(root)
            }

            Spacer(minLength: 8)

            if isRemoving {
                ProgressView().controlSize(.small)
            } else {
                Button(role: .destructive, action: onRemove) {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
                .disabled(isDisabled)
                .help("Remove this folder from the index")
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Shortcuts

private struct ShortcutsSettingsTab: View {
    var body: some View {
        Form {
            Section {
                // The package's own view. Recording writes through to the same
                // `KeyboardShortcuts_summonOasis` UserDefaults key the `.summonOasis`
                // Name reads, and `onKeyDown` re-registers, so a rebind takes
                // effect immediately and survives relaunch with nothing here to
                // save.
                KeyboardShortcuts.Recorder("Summon Oasis:", name: .summonOasis)

                Text("Press this from any app to open the Oasis search panel. The default is ⌘⌥O.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                // The reason this control exists rather than being a nicety.
                // Measured 2026-07-27: two GUI processes can register the same
                // combination and *both* get `noErr`, so a third-party app's
                // hotkey is invisible to any probe. The collision shows up only
                // as the key doing nothing — and rebinding is the entire
                // recourse, which is why the binding was built as a default
                // rather than hardcoded.
                Text("If the shortcut does nothing, another app has probably claimed it. macOS can't report that collision to Oasis, so rebinding here is the fix.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .formStyle(.grouped)
    }
}

// MARK: - About

private struct AboutSettingsTab: View {
    /// Read from the bundle, never a literal: a hardcoded string is a version
    /// that silently stops matching the build the moment `MARKETING_VERSION`
    /// changes.
    private var version: String {
        let short = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        return build.map { "\(short) (\($0))" } ?? short
    }

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "sparkle.magnifyingglass")
                .font(.system(size: 44))
                .foregroundStyle(.tint)

            Text("Oasis")
                .font(.title2.weight(.semibold))

            Text("Version \(version)")
                .font(.callout)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)

            Text("Local semantic search for your own files. Nothing leaves this machine.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            Link("github.com/oasis", destination: URL(string: "https://github.com/mohamedelrefaei/oasis")!)
                .font(.callout)
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
