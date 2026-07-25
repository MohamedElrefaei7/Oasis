//
//  OasisApp.swift
//  Oasis
//
//  Created by Mohamed Elrefaei on 7/25/26.
//

import AppKit
import SwiftUI

@main
struct OasisApp: App {
    /// The delegate owns the controller because the delegate is the only thing
    /// that gets told the app is quitting — see `applicationWillTerminate`.
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView(controller: appDelegate.controller)
        }
        .windowResizability(.contentSize)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let controller = ServerController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller.start()
    }

    /// Teardown path #1 of 2: explicit SIGTERM on a clean quit (⌘Q).
    ///
    /// This does **not** run when Xcode stops the app (⌘.) — that's a SIGKILL,
    /// and nothing in-process gets to run. The `--managed` watchdog we spawn the
    /// child with covers exactly that case (docs/APP_SEAM.md §5); see the
    /// comment on `ServerController.terminateChild()` for why both mechanisms
    /// are load-bearing.
    func applicationWillTerminate(_ notification: Notification) {
        controller.shutdown()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}
