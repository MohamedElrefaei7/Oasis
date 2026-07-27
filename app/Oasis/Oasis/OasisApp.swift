//
//  OasisApp.swift
//  Oasis
//
//  Created by Mohamed Elrefaei on 7/25/26.
//

import AppKit
import KeyboardShortcuts
import SwiftUI

@main
struct OasisApp: App {
    /// The delegate owns the controller because the delegate is the only thing
    /// that gets told the app is quitting — see `applicationWillTerminate`.
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        // A `Window`, not a `WindowGroup`. The summon hand-off has to reopen the
        // main window after the user closed it, and `openWindow(id:)` against a
        // `WindowGroup` opens a *second* window every time rather than restoring
        // the one that was closed. Oasis has exactly one main window, so the
        // single-window scene is also the honest model of the app.
        Window("Oasis", id: AppSearchCoordinator.mainWindowID) {
            ContentView(coordinator: appDelegate.coordinator)
        }
        .windowResizability(.contentSize)

        // Part D: the resident presence. With the app no longer terminating on
        // last-window-close, this is what the user sees when no window is open —
        // and a discoverable second route to the panel for anyone who never
        // learns the hotkey.
        MenuBarExtra {
            MenuBarContent(appDelegate: appDelegate)
        } label: {
            MenuBarLabel(coordinator: appDelegate.coordinator)
        }
    }
}

// MARK: - Menu bar

private struct MenuBarContent: View {
    let appDelegate: AppDelegate

    var body: some View {
        Button("Open Oasis") {
            appDelegate.coordinator.showMainWindow()
        }

        Button("Search…") {
            appDelegate.summon.show()
        }

        Divider()

        // Quit — and quit still means quit. `applicationWillTerminate` fires,
        // which is where the server child is torn down.
        Button("Quit Oasis") {
            NSApp.terminate(nil)
        }
        .keyboardShortcut("q")
    }
}

/// The status-bar icon — and the app's most reliable handle on SwiftUI's
/// `openWindow`.
///
/// `OpenWindowAction` can only be read out of a view's environment, but the
/// hand-off needs it from `AppDelegate`, which is not a view. The menu-bar label
/// is the one view guaranteed to exist for the whole process lifetime —
/// `ContentView` is not, that being the entire point of Part D — so it captures
/// the action on the way past.
private struct MenuBarLabel: View {
    let coordinator: AppSearchCoordinator

    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Image(systemName: "sparkle.magnifyingglass")
            .onAppear { coordinator.registerOpenWindow(openWindow) }
    }
}

// MARK: - Delegate

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let controller = ServerController()

    /// App-level search state plus the window plumbing the summon panel writes
    /// through. `lazy` so it can see `controller`.
    lazy var coordinator = AppSearchCoordinator(controller: controller)
    lazy var summon = SummonPanelController(coordinator: coordinator)

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller.start()
        registerSummonHotkey()
    }

    // MARK: Part A — the global hotkey

    /// A registered hotkey is a Carbon `RegisterEventHotKey`, which needs **no
    /// Accessibility permission** — unlike an event tap, which does. There is
    /// nothing to prompt for and nothing to grant; ⌘⌥O works on first launch.
    private func registerSummonHotkey() {
        // Probe before registering: afterwards the combination is ours, and the
        // probe would only be measuring our own registration.
        SummonHotkey.logProbe(SummonHotkey.probe())

        KeyboardShortcuts.onKeyDown(for: .summonOasis) { [weak self] in
            // Carbon dispatches this on the main thread; state the isolation
            // rather than hopping, so the panel appears on the same turn of the
            // run loop as the key press.
            MainActor.assumeIsolated {
                self?.summon.toggle()
            }
        }
    }

    // MARK: Part D — residency

    /// **`false` is what makes the global summon global.**
    ///
    /// Closing the main window leaves Oasis running in the menu bar with the
    /// hotkey still registered, so ⌘⌥O works from anywhere with no Oasis window
    /// on screen. The server child's lifetime is tied to the *app*, not the
    /// window, so it stays up too — which is also what keeps the summon fast:
    /// the models are already loaded and warm.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// Clicking the Dock icon with no window open should bring the window back,
    /// not activate an app with nothing on screen.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows {
            coordinator.showMainWindow()
        }
        return true
    }

    /// Teardown path #1 of 2: explicit SIGTERM on a clean quit (⌘Q).
    ///
    /// This does **not** run when Xcode stops the app (⌘.) — that's a SIGKILL,
    /// and nothing in-process gets to run. The `--managed` watchdog we spawn the
    /// child with covers exactly that case (docs/APP_SEAM.md §5); see the
    /// comment on `ServerController.terminateChild()` for why both mechanisms
    /// are load-bearing.
    ///
    /// Window-close is not quit (see above), so this stays the only path that
    /// kills the server: quit tears everything down, closing a window tears
    /// down nothing.
    func applicationWillTerminate(_ notification: Notification) {
        controller.shutdown()
    }
}
