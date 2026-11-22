import ARKit
import AVFoundation
import NammaCaptureCore

/// Exposure / white-balance locking through the only supported route while ARKit owns the camera:
/// ARWorldTrackingConfiguration.configurableCaptureDeviceForPrimaryCamera (iOS 16+; nil on devices without an
/// ultra-wide camera). No parallel AVCaptureSession is opened. Every request is read back from the device and
/// recorded as what actually happened ("locked" only if the device reports the locked mode afterwards).
enum CameraControls {
    static func setLocked(_ lock: Bool) -> [CameraControlResult] {
        let now = ISOTime.string()
        guard let dev = ARWorldTrackingConfiguration.configurableCaptureDeviceForPrimaryCamera else {
            let why = "ARKit exposes no configurable capture device on this phone"
            return [CameraControlResult(control: "exposure_lock", outcome: "unavailable", detail: why, at: now),
                    CameraControlResult(control: "white_balance_lock", outcome: "unavailable", detail: why, at: now)]
        }
        do {
            try dev.lockForConfiguration()
        } catch {
            return [CameraControlResult(control: "exposure_lock", outcome: "failed", detail: error.localizedDescription, at: now),
                    CameraControlResult(control: "white_balance_lock", outcome: "failed", detail: error.localizedDescription, at: now)]
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
        let expOK = dev.exposureMode == expMode
        let wbOK = dev.whiteBalanceMode == wbMode
        let gains = dev.deviceWhiteBalanceGains
        return [
            CameraControlResult(control: "exposure_lock", outcome: outcome(supported: expSupported, ok: expOK),
                                detail: String(format: "read back: mode=%d duration=%.5fs iso=%.0f", dev.exposureMode.rawValue,
                                               CMTimeGetSeconds(dev.exposureDuration), dev.iso), at: now),
            CameraControlResult(control: "white_balance_lock", outcome: outcome(supported: wbSupported, ok: wbOK),
                                detail: String(format: "read back: mode=%d gains r=%.3f g=%.3f b=%.3f", dev.whiteBalanceMode.rawValue,
                                               gains.redGain, gains.greenGain, gains.blueGain), at: now),
        ]
    }
}
