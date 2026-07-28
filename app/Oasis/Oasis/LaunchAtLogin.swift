//
//  LaunchAtLogin.swift
//  Oasis
//
//  Settings ▸ General. `SMAppService.mainApp` — the modern replacement for the
//  deprecated `SMLoginItemSetEnabled` and for writing a login-item plist by
//  hand. Registering the *main app* needs no helper target and no extra
//  entitlement; macOS registers the bundle itself.
//
//  Natural for a menu-bar-resident app: the whole point of Part D's residency
//  is that ⌘⌥O works with no Oasis window open, and that only holds if Oasis is
//  running — which, for most users, means starting at login.
//

import Foundation
import OSLog
import Observation
import ServiceManagement

@MainActor
@Observable
final class LaunchAtLogin {

    /// Whether the login item is registered, read from the system rather than
    /// mirrored in `UserDefaults`.
    ///
    /// **`SMAppService` is the source of truth and a stored bool would drift**:
    /// the user can revoke the item in System Settings ▸ General ▸ Login Items
    /// without the app running, and a mirrored preference would then show a
    /// toggle that is on while the item is off.
    private(set) var isEnabled: Bool = false

    /// Set when register/unregister failed, which in development is the common
    /// case rather than the exceptional one — see `setEnabled`.
    private(set) var message: String?

    private static let log = Logger(subsystem: "com.oasis.app", category: "launch-at-login")

    init() {
        refresh()
    }

    func refresh() {
        isEnabled = SMAppService.mainApp.status == .enabled
    }

    /// Register or unregister, then re-read the real status.
    ///
    /// **Failure is reported, never swallowed, and the toggle follows the
    /// system.** `register()` throws for an app running out of DerivedData
    /// (Xcode's Run), because macOS will not create a login item for a bundle
    /// in a location it considers transient. That is expected in development
    /// and not a bug to hide: the alternative — leaving the toggle visually on
    /// after a failed registration — would be a control that lies. `refresh()`
    /// at the end is what makes the toggle snap back.
    func setEnabled(_ enabled: Bool) {
        message = nil
        do {
            if enabled {
                try SMAppService.mainApp.register()
                Self.log.notice("registered login item")
            } else {
                try SMAppService.mainApp.unregister()
                Self.log.notice("unregistered login item")
            }
        } catch {
            Self.log.error("login item \(enabled ? "register" : "unregister", privacy: .public) failed: \(error.localizedDescription, privacy: .public)")
            message = enabled
                ? "Couldn't turn this on — \(error.localizedDescription) (expected when running from Xcode; works in the installed app.)"
                : "Couldn't turn this off — \(error.localizedDescription)"
        }
        refresh()
    }

    /// A human-readable form of the raw status, for the cases the toggle alone
    /// can't express — most usefully `.requiresApproval`, where registration
    /// succeeded but the user has the item switched off in System Settings, so
    /// nothing this app does will turn it on.
    var statusDescription: String? {
        switch SMAppService.mainApp.status {
        case .requiresApproval:
            "Approve Oasis in System Settings ▸ General ▸ Login Items to finish enabling this."
        case .notFound:
            "macOS has no record of this app bundle yet — expected when running from Xcode."
        default:
            nil
        }
    }
}
