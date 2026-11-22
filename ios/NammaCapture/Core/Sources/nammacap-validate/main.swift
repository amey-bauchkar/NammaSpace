import Foundation
import NammaCaptureCore

// swift run nammacap-validate <file.nammacap> [...]
// Exit 0 when every archive is valid, 2 otherwise.
let args = CommandLine.arguments.dropFirst()
guard !args.isEmpty else {
    FileHandle.standardError.write(Data("usage: nammacap-validate <file.nammacap> [...]\n".utf8))
    exit(64)
}
var allOK = true
for a in args {
    let r = ArchiveValidator.validate(URL(fileURLWithPath: a))
    print("\(a): \(r)")
    allOK = allOK && r.ok
}
exit(allOK ? 0 : 2)
