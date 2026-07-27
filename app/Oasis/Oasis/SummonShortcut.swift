//
//  SummonShortcut.swift
//  Oasis
//
//  The global hotkey's name and its default binding (⌘⌥O), plus a probe that
//  answers the one question the KeyboardShortcuts package won't: did the
//  registration actually take, or is another app already holding this key?
//

// AppKit is imported for `NSEvent.ModifierFlags` — the target builds with
// `SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY`, so KeyboardShortcuts
// re-exporting it is not enough.
import AppKit
import Carbon.HIToolbox
import KeyboardShortcuts
import OSLog

extension KeyboardShortcuts.Name {
    /// The Spotlight-style summon.
    ///
    /// **⌘⌥O is a default, not a hard binding.** The package persists the
    /// user's choice under `KeyboardShortcuts_summonOasis` in `UserDefaults`
    /// and only writes this default when that key is absent, so Settings can
    /// hand the user a `KeyboardShortcuts.Recorder` later and rebinding will
    /// stick across launches without anything here changing.
    static let summonOasis = Self(
        "summonOasis",
        default: .init(.o, modifiers: [.command, .option])
    )
}

/// Registration diagnostics for the summon hotkey.
///
/// `CarbonKeyboardShortcuts.register` **swallows a failed `RegisterEventHotKey`
/// and returns** — no throw, no log, no return value. The failure mode is
/// therefore a hotkey that silently does nothing, which is indistinguishable
/// from a bug in the panel. This probe runs the same registration ourselves,
/// releases it immediately, and reports the `OSStatus`.
///
/// **What a probe can and cannot see — measured, 2026-07-27.** Two GUI
/// processes were made to register ⌘⌥O at the same time: both got `noErr`.
/// `RegisterEventHotKey` therefore does **not** report another *application's*
/// hotkey, and `eventHotKeyExistsErr` (-9878) means only "this process already
/// registered this combination". So:
///
/// - A **system** reservation is detectable, via `CopySymbolicHotKeys` — and
///   it's the collision that actually wins, since macOS's own bindings take the
///   key first. That check is worth having.
/// - A **third-party app's** hotkey is invisible to any probe. It shows up as
///   ⌘⌥O doing nothing, and the fix is the same either way: rebind, which the
///   default is designed to allow.
enum SummonHotkey {

    enum Registration: CustomStringConvertible {
        /// `RegisterEventHotKey` accepted the combination.
        case clean
        /// macOS itself reserves the combination (System Settings ▸ Keyboard).
        case systemReserved
        /// Carbon refused it — `-9878` is `eventHotKeyExistsErr`.
        case failed(OSStatus)
        /// No shortcut is bound at all (the user cleared it in Settings).
        case unbound

        var description: String {
            switch self {
            case .clean: "registered cleanly"
            case .systemReserved: "collides with a system shortcut"
            case .failed(let status): "RegisterEventHotKey failed (OSStatus \(status))"
            case .unbound: "no shortcut bound"
            }
        }
    }

    private static let log = Logger(subsystem: "com.oasis.app", category: "hotkey")

    /// Probe **before anything in the process touches
    /// `KeyboardShortcuts.Name.summonOasis`** — including reading it.
    ///
    /// This bit is genuinely surprising, and it produced a false `-9878` on
    /// first launch before it was understood: `Name.init` writes its default
    /// into `UserDefaults` when no binding is stored yet, and the package's
    /// `userDefaultsSet` **calls `register(shortcut)` on the way past**. So
    /// merely mentioning `.summonOasis` registers the Carbon hotkey, and a
    /// probe that reads the binding through the package is already probing
    /// against our own registration — reporting a collision on the first launch
    /// and "clean" on every launch after, which is worse than not probing.
    ///
    /// Hence reading the binding straight out of `UserDefaults`: it is the
    /// package's own documented storage, and it does not register anything.
    static func probe() -> Registration {
        guard let shortcut = storedBinding() else {
            return .unbound
        }

        // The one collision a probe can actually see: macOS's own reservations
        // (Spotlight's ⌘Space, Mission Control, and friends), which win the key
        // outright.
        if systemReservations().contains(where: {
            $0.keyCode == shortcut.keyCode && $0.modifiers == shortcut.modifiers
        }) {
            return .systemReserved
        }

        var reference: EventHotKeyRef?
        let status = RegisterEventHotKey(
            UInt32(shortcut.keyCode),
            UInt32(shortcut.modifiers),
            // A signature distinct from the package's own, so this probe can
            // never be mistaken for the real registration.
            EventHotKeyID(signature: OSType(0x4F41_5350), id: 1),  // 'OASP'
            GetEventDispatcherTarget(),
            0,
            &reference
        )
        if let reference {
            UnregisterEventHotKey(reference)
        }

        return status == noErr ? .clean : .failed(status)
    }

    /// Log the outcome once at launch. A collision is **not fatal** — the
    /// binding is rebindable — so this is a notice, not a failure path.
    static func logProbe(_ registration: Registration) {
        let binding = storedBinding()
            .map { "keyCode \($0.keyCode) / carbonModifiers \($0.modifiers)" } ?? "none"
        switch registration {
        case .clean:
            log.notice("summon hotkey (\(binding, privacy: .public)) — registered cleanly")
        default:
            log.error("summon hotkey (\(binding, privacy: .public)) — \(registration.description, privacy: .public); rebind it in Settings")
        }
    }

    /// The bound shortcut, read from the package's storage without going
    /// through `KeyboardShortcuts` (see `probe()` for why that matters).
    ///
    /// `KeyboardShortcuts_<name>` holds either the JSON encoding of `Shortcut`
    /// — whose only stored properties are `carbonKeyCode` and `carbonModifiers`
    /// — or the boolean `false` when the user has cleared a shortcut that has a
    /// default. Absent entirely means nothing has been written yet, so the
    /// default this file declares is what will be registered.
    private static func storedBinding() -> (keyCode: Int, modifiers: Int)? {
        let key = "KeyboardShortcuts_summonOasis"
        guard let stored = UserDefaults.standard.object(forKey: key) else {
            return (kVK_ANSI_O, cmdKey | optionKey)  // ⌘⌥O, the declared default
        }
        guard
            let json = (stored as? String)?.data(using: .utf8),
            let decoded = try? JSONDecoder().decode([String: Int].self, from: json),
            let keyCode = decoded["carbonKeyCode"],
            let modifiers = decoded["carbonModifiers"]
        else {
            return nil  // present but not a shortcut → the user cleared it
        }
        return (keyCode, modifiers)
    }

    private static func systemReservations() -> [(keyCode: Int, modifiers: Int)] {
        var unmanaged: Unmanaged<CFArray>?
        guard
            CopySymbolicHotKeys(&unmanaged) == noErr,
            let raw = unmanaged?.takeRetainedValue() as? [[String: Any]]
        else {
            return []
        }

        return raw.compactMap { entry in
            guard
                (entry[kHISymbolicHotKeyEnabled] as? Bool) == true,
                let keyCode = entry[kHISymbolicHotKeyCode] as? Int,
                let modifiers = entry[kHISymbolicHotKeyModifiers] as? Int
            else {
                return nil
            }
            return (keyCode, modifiers)
        }
    }
}
