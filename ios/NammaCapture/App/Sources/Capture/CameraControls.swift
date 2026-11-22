import ARKit
import AVFoundation
import NammaCaptureCore

/// Exposure / white-balance / focus control through the only supported route while ARKit owns the camera:
/// ARWorldTrackingConfiguration.configurableCaptureDeviceForPrimaryCamera (iOS 16+; nil on devices without an
/// ultra-wide camera). No parallel AVCaptureSession is opened.
///
/// Every attempt produces one CameraControlResult per control (exposure_lock, white_balance_lock, focus_lock) whose
/// outcome comes from reading the device back after the change: "locked" only if the device reports the locked mode.
/// Field finding (Testing Room, 2026-11-22, iPhone18,1): app 1.0 only locked when the user opened a menu, so that
/// capture recorded no outcome at all (ISO 64-8000 across frames). The app now locks automatically once exposure has
/// settled, and records outcomes at session start even when locking is impossible.
enum CameraControls {
    /// Focus stays automatic: a room scan needs both near (furniture) and far (walls) surfaces sharp, and a locked focus
    /// distance would blur one of them. The decision is recorded so it is never mistaken for a missing record.
    static let focusPolicy = "autofocus kept on: rooms need near and far surfaces in focus"

    static var device: AVCaptureDevice? { ARWorldTrackingConfiguration.configurableCaptureDeviceForPrimaryCamera }

    /// Outcomes to record at session start when the device offers no configurable camera.
    static func unavailableAtStart() -> [CameraControlResult] {
        let now = ISOTime.string()
        let why = "ARKit exposes no configurable capture device on this phone (configurableCaptureDeviceForPrimaryCamera is nil)"
        return [CameraControlResult(control: "exposure_lock", outcome: "unavailable", detail: why, at: now),
                CameraControlResult(control: "white_balance_lock", outcome: "unavailable", detail: why, at: now),
                CameraControlResult(control: "focus_lock", outcome: "unavailable", detail: why, at: now)]
    }

    /// true while the camera is still converging; locking then would freeze a transient value.
    static var isAdjusting: Bool {
        guard let d = device else { return false }
        return d.isAdjustingExposure || d.isAdjustingWhiteBalance
    }

    /// Lock (or unlock) exposure and white balance; focus is left automatic and recorded as such.
    static func setLocked(_ lock: Bool, reason: String) -> [CameraControlResult] {
        let now = ISOTime.string()
        guard let dev = device else { return unavailableAtStart() }
        do {
            try dev.lockForConfiguration()
        } catch {
            let msg = "\(reason); lockForConfiguration failed: \(error.localizedDescription)"
            return [CameraControlResult(control: "exposure_lock", outcome: "failed", detail: msg, at: now),
                    CameraControlResult(control: "white_balance_lock", outcome: "failed", detail: msg, at: now),
                    CameraControlResult(control: "focus_lock", outcome: "not_requested", detail: focusPolicy, at: now)]
        }
        let expMode: AVCaptureDevice.ExposureMode = lock ? .locked : .continuousAutoExposure
        let wbMode: AVCaptureDevice.WhiteBalanceMode = lock ? .locked : .continuousAutoWhiteBalance
        let expSupported = dev.isExposureModeSupported(expMode)
        let wbSupported = dev.isWhiteBalanceModeSupported(wbMode)
        if expSupported { dev.exposureMode = expMode }
        if wbSupported { dev.whiteBalanceMode = wbMode }
        dev.unlockForConfiguration()

        func outcome(supported: Bool, ok: Bool) -> String {
            guard supported else { return "unavailable" }
            if !lock { return ok ? "disabled" : "failed" }
            return ok ? "locked" : "failed"
        }
        let g = dev.deviceWhiteBalanceGains
        return [
            CameraControlResult(control: "exposure_lock", outcome: outcome(supported: expSupported, ok: dev.exposureMode == expMode),
                                detail: String(format: "%@; read back: mode=%d duration=%.5fs iso=%.0f", reason,
                                               dev.exposureMode.rawValue, CMTimeGetSeconds(dev.exposureDuration), dev.iso), at: now),
            CameraControlResult(control: "white_balance_lock", outcome: outcome(supported: wbSupported, ok: dev.whiteBalanceMode == wbMode),
                                detail: String(format: "%@; read back: mode=%d gains r=%.3f g=%.3f b=%.3f", reason,
                                               dev.whiteBalanceMode.rawValue, g.redGain, g.greenGain, g.blueGain), at: now),
            CameraControlResult(control: "focus_lock", outcome: "not_requested",
                                detail: "\(focusPolicy); read back: focusMode=\(dev.focusMode.rawValue)", at: now),
        ]
    }

    /// Current state as the device reports it (nil when there is no configurable device).
    static func readBack() -> (exposureLocked: Bool, whiteBalanceLocked: Bool)? {
        guard let d = device else { return nil }
        return (d.exposureMode == .locked, d.whiteBalanceMode == .locked)
    }
}
