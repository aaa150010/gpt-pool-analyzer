import Cocoa
import Security
import Sparkle
import Vision

private let maxBalanceHistoryCount = 1000
private let maxPoolHistoryPerGroup = 1000
private let defaultPollingMinutes: Double = 5
private let defaultBalanceBaseURL = "https://api.ai-pixel.online"
private let analyzerServerBaseURL = "https://lynote.xyz/gpt-api"

private func drawTimeAxis(in rect: NSRect, minDate: Date, maxDate: Date, tickCount: Int = 5) {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "zh_CN")
    formatter.dateFormat = Calendar.current.isDate(minDate, inSameDayAs: maxDate) ? "HH:mm" : "MM-dd HH:mm"
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 11, weight: .medium),
        .foregroundColor: NSColor.secondaryLabelColor,
        .paragraphStyle: paragraph
    ]
    let count = max(tickCount, 2)
    let span = max(maxDate.timeIntervalSince(minDate), 1)
    NSColor.separatorColor.withAlphaComponent(0.35).setStroke()
    for index in 0..<count {
        let ratio = CGFloat(index) / CGFloat(count - 1)
        let x = rect.minX + ratio * rect.width
        let tick = NSBezierPath()
        tick.lineWidth = 0.8
        tick.move(to: NSPoint(x: x, y: rect.maxY))
        tick.line(to: NSPoint(x: x, y: rect.maxY + 5))
        tick.stroke()
        let date = minDate.addingTimeInterval(TimeInterval(ratio) * span)
        let label = formatter.string(from: date)
        label.draw(in: NSRect(x: x - 45, y: rect.maxY + 8, width: 90, height: 16), withAttributes: attrs)
    }
}

struct Snapshot: Codable {
    var date: Date
    var total: Double
    var amounts: [Double]
    var accounts: [String]?
}

struct OCRResult {
    var text: String
    var amounts: [Double]
    var accounts: [String]
    var lines: [OCRLine]
}

struct OCRLine {
    var text: String
    var boundingBox: CGRect
}

struct StoredState: Codable {
    var cost: Double
    var partnerCost: Double?
    var manualBaseTotal: Double?
    var withdrawalAmount: Double?
    var useBaseDeduction: Bool?
    var baseDeductionAmount: Double?
    var initial: Snapshot?
    var history: [Snapshot]
    var costAdditions: [CostAddition]?
    var settlement: SettlementState?
}

struct SettlementState: Codable {
    var partnerName: String
    var partnerSharePercent: Double
    var payoutRatePercent: Double
    var withdrawals: [String: Double]

    static let `default` = SettlementState(
        partnerName: "社会哥",
        partnerSharePercent: 40,
        payoutRatePercent: 85,
        withdrawals: [:]
    )
}

struct CostAddition: Codable {
    var id: String
    var date: Date
    var note: String
    var amount: Double
    var createdAt: Date
}

struct PoolSnapshot: Codable {
    var date: Date
    var groupName: String
    var status: String
    var total: Int
    var active: Int
    var schedulable: Int
    var remaining5h: Int?
    var remaining7d: Int?
    var utilization5h: Double?
    var utilization7d: Double?
    var concurrentAvailable: Int
    var concurrentTotal: Int
    var limited: Int
    var quotaProtected: Int
    var error: Int
    var disabled: Int
}

struct PoolAnalyzerState: Codable {
    var history: [PoolSnapshot]
    var selectedGroups: [String]?
    var availableGroups: [String]?
    var pollingMinutes: Double?
    var warningEmail: String?
    var accessToken: String?
    var refreshToken: String?
}

struct APIQuotaDashboardResponse: Decodable {
    var data: APIQuotaDashboardData
}

struct APIQuotaDashboardData: Decodable {
    var platform: APIQuotaPlatform
}

struct APIQuotaPlatform: Decodable {
    var groupSummaries: [APIGroupSummary]

    enum CodingKeys: String, CodingKey {
        case groupSummaries = "group_summaries"
    }
}

struct APIGroupSummary: Decodable {
    var groupName: String
    var groupStatus: String
    var accountCount: Int
    var activeAccountCount: Int
    var schedulableAccountCount: Int
    var rateLimitedAccountCount: Int
    var codexQuotaProtectedAccountCount: Int
    var errorAccountCount: Int
    var disabledAccountCount: Int
    var usageWindows: [APIUsageWindow]

    enum CodingKeys: String, CodingKey {
        case groupName = "group_name"
        case groupStatus = "group_status"
        case accountCount = "account_count"
        case activeAccountCount = "active_account_count"
        case schedulableAccountCount = "schedulable_account_count"
        case rateLimitedAccountCount = "rate_limited_account_count"
        case codexQuotaProtectedAccountCount = "codex_quota_protected_account_count"
        case errorAccountCount = "error_account_count"
        case disabledAccountCount = "disabled_account_count"
        case usageWindows = "usage_windows"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        groupName = try container.decodeIfPresent(String.self, forKey: .groupName) ?? ""
        groupStatus = try container.decodeIfPresent(String.self, forKey: .groupStatus) ?? ""
        accountCount = try container.decodeFlexibleInt(forKey: .accountCount)
        activeAccountCount = try container.decodeFlexibleInt(forKey: .activeAccountCount)
        schedulableAccountCount = try container.decodeFlexibleInt(forKey: .schedulableAccountCount)
        rateLimitedAccountCount = try container.decodeFlexibleInt(forKey: .rateLimitedAccountCount)
        codexQuotaProtectedAccountCount = try container.decodeFlexibleInt(forKey: .codexQuotaProtectedAccountCount)
        errorAccountCount = try container.decodeFlexibleInt(forKey: .errorAccountCount)
        disabledAccountCount = try container.decodeFlexibleInt(forKey: .disabledAccountCount)
        usageWindows = try container.decodeIfPresent([APIUsageWindow].self, forKey: .usageWindows) ?? []
    }
}

struct APIUsageWindow: Decodable {
    var window: String
    var accountCount: Int?
    var remainingCapacityPercent: Double?
    var averageUtilization: Double?

    enum CodingKeys: String, CodingKey {
        case window
        case accountCount = "account_count"
        case remainingCapacityPercent = "remaining_capacity_percent"
        case averageUtilization = "average_utilization"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        window = try container.decodeIfPresent(String.self, forKey: .window) ?? ""
        accountCount = try container.decodeFlexibleOptionalInt(forKey: .accountCount)
        remainingCapacityPercent = try container.decodeFlexibleOptionalDouble(forKey: .remainingCapacityPercent)
        averageUtilization = try container.decodeFlexibleOptionalDouble(forKey: .averageUtilization)
    }
}

struct APILoginResponse: Decodable {
    var data: APILoginData
}

struct APIErrorResponse: Decodable {
    var message: String?
    var code: Int?
}

struct APILoginData: Decodable {
    var accessToken: String
    var refreshToken: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
    }
}

struct PoolCredentials: Codable {
    var email: String
    var password: String
}

struct SMTPSettings: Codable {
    var host: String
    var port: Int
    var username: String
    var password: String
    var recipient: String

    static let `default` = SMTPSettings(
        host: "smtp.qq.com",
        port: 465,
        username: "",
        password: "",
        recipient: "706137702@qq.com"
    )
}

struct BalanceAccount: Codable {
    var name: String
    var baseURL: String
    var apiKey: String
}

struct BalanceQueryItem {
    var account: BalanceAccount
    var balance: Double
    var unit: String
}

struct ServerBootstrapPayload: Codable {
    var storedState: StoredState
    var poolState: PoolAnalyzerState
    var balanceAccounts: [BalanceAccount]
    var poolCredentials: PoolCredentials?
    var smtpSettings: SMTPSettings
}

struct ServerStateResponse: Codable {
    var initialized: Bool
    var storedState: StoredState?
    var poolState: PoolAnalyzerState?
    var balanceAccounts: [BalanceAccount]?
}

struct ServerRefreshResponse: Codable {
    var ok: Bool
    var state: ServerStateResponse?
}

enum PoolTrendMetric: Int, CaseIterable {
    case remaining5h
    case remaining7d
    case total
    case limited
    case quotaProtected
    case error
    case concurrent

    var title: String {
        switch self {
        case .remaining5h: return "5h剩余"
        case .remaining7d: return "7d剩余"
        case .total: return "总账号"
        case .limited: return "限流"
        case .quotaProtected: return "额度保护"
        case .error: return "错误"
        case .concurrent: return "并发可用"
        }
    }

    func value(in snapshot: PoolSnapshot) -> Double? {
        switch self {
        case .remaining5h: return snapshot.remaining5h.map(Double.init)
        case .remaining7d: return snapshot.remaining7d.map(Double.init)
        case .total: return Double(snapshot.total)
        case .limited: return Double(snapshot.limited)
        case .quotaProtected: return Double(snapshot.quotaProtected)
        case .error: return Double(snapshot.error)
        case .concurrent: return Double(snapshot.concurrentAvailable)
        }
    }
}

final class PoolTrendChartView: NSView {
    var history: [PoolSnapshot] = [] {
        didSet { needsDisplay = true }
    }
    var groups: [String] = [] {
        didSet { needsDisplay = true }
    }
    var metric: PoolTrendMetric = .remaining5h {
        didSet { needsDisplay = true }
    }

    private let palette: [NSColor] = [.systemTeal, .systemOrange, .systemBlue, .systemPurple, .systemPink, .systemGreen, .systemRed, .systemIndigo]
    private var trackingArea: NSTrackingArea?
    private var hoverLocation: NSPoint?

    override var isFlipped: Bool { true }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let trackingArea {
            removeTrackingArea(trackingArea)
        }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.mouseMoved, .mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        trackingArea = area
    }

    override func mouseMoved(with event: NSEvent) {
        hoverLocation = convert(event.locationInWindow, from: nil)
        needsDisplay = true
    }

    override func mouseExited(with event: NSEvent) {
        hoverLocation = nil
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        NSColor.white.setFill()
        bounds.fill()

        let plotRect = bounds.insetBy(dx: 58, dy: 42)
        guard plotRect.width > 80, plotRect.height > 80 else { return }

        let series = makeSeries()
        guard !series.isEmpty else {
            drawEmpty(in: bounds)
            return
        }

        let allPoints = series.flatMap(\.points)
        guard let minDate = allPoints.map(\.date).min(),
              let maxDate = allPoints.map(\.date).max() else {
            drawEmpty(in: bounds)
            return
        }
        let values = allPoints.map(\.value)
        let rawMin = values.min() ?? 0
        let rawMax = values.max() ?? 1
        let padding = max((rawMax - rawMin) * 0.12, rawMax == rawMin ? max(rawMax * 0.08, 1) : 0)
        let minValue = max(0, rawMin - padding)
        let maxValue = rawMax + padding
        let timeSpan = max(maxDate.timeIntervalSince(minDate), 1)
        let valueSpan = max(maxValue - minValue, 1)

        drawGrid(in: plotRect, minValue: minValue, maxValue: maxValue)
        drawAxes(in: plotRect)

        for (index, item) in series.enumerated() {
            let color = palette[index % palette.count]
            let path = NSBezierPath()
            path.lineWidth = 2.4
            path.lineJoinStyle = .round
            for (pointIndex, point) in item.points.enumerated() {
                let x = plotRect.minX + CGFloat(point.date.timeIntervalSince(minDate) / timeSpan) * plotRect.width
                let y = plotRect.maxY - CGFloat((point.value - minValue) / valueSpan) * plotRect.height
                if pointIndex == 0 {
                    path.move(to: NSPoint(x: x, y: y))
                } else {
                    path.line(to: NSPoint(x: x, y: y))
                }
            }
            color.setStroke()
            path.stroke()
            if let last = item.points.last {
                let x = plotRect.minX + CGFloat(last.date.timeIntervalSince(minDate) / timeSpan) * plotRect.width
                let y = plotRect.maxY - CGFloat((last.value - minValue) / valueSpan) * plotRect.height
                color.setFill()
                NSBezierPath(ovalIn: NSRect(x: x - 3.5, y: y - 3.5, width: 7, height: 7)).fill()
            }
        }

        drawTimeAxis(in: plotRect, minDate: minDate, maxDate: maxDate)
        drawLegend(series: series, in: NSRect(x: plotRect.minX, y: bounds.minY + 8, width: plotRect.width, height: 24))
        drawTooltipIfNeeded(in: plotRect, minDate: minDate, timeSpan: timeSpan, minValue: minValue, valueSpan: valueSpan)
    }

    private func makeSeries() -> [(group: String, points: [(date: Date, value: Double)])] {
        let orderedGroups = groups.isEmpty
            ? Array(Set(history.map(\.groupName))).sorted()
            : groups
        return orderedGroups.compactMap { group in
            let points = history
                .filter { $0.groupName == group }
                .sorted { $0.date < $1.date }
                .compactMap { snapshot -> (date: Date, value: Double)? in
                    guard let value = metric.value(in: snapshot) else { return nil }
                    return (snapshot.date, value)
                }
            return points.isEmpty ? nil : (group, points)
        }
    }

    private func drawGrid(in rect: NSRect, minValue: Double, maxValue: Double) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .right
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11, weight: .medium),
            .foregroundColor: NSColor.secondaryLabelColor,
            .paragraphStyle: paragraph
        ]
        NSColor.separatorColor.withAlphaComponent(0.6).setStroke()
        for index in 0...4 {
            let ratio = CGFloat(index) / 4
            let y = rect.maxY - ratio * rect.height
            let path = NSBezierPath()
            path.lineWidth = 0.8
            path.move(to: NSPoint(x: rect.minX, y: y))
            path.line(to: NSPoint(x: rect.maxX, y: y))
            path.stroke()
            let value = minValue + Double(ratio) * (maxValue - minValue)
            let label = value >= 100 ? "\(Int(value.rounded()))" : String(format: "%.1f", value)
            label.draw(in: NSRect(x: 4, y: y - 8, width: rect.minX - 10, height: 16), withAttributes: attrs)
        }
    }

    private func drawAxes(in rect: NSRect) {
        NSColor.separatorColor.setStroke()
        let path = NSBezierPath()
        path.lineWidth = 1
        path.move(to: NSPoint(x: rect.minX, y: rect.minY))
        path.line(to: NSPoint(x: rect.minX, y: rect.maxY))
        path.line(to: NSPoint(x: rect.maxX, y: rect.maxY))
        path.stroke()
    }

    private func drawLegend(series: [(group: String, points: [(date: Date, value: Double)])], in rect: NSRect) {
        var x = rect.minX
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .semibold),
            .foregroundColor: NSColor.labelColor
        ]
        for (index, item) in series.enumerated() {
            let color = palette[index % palette.count]
            color.setFill()
            NSBezierPath(roundedRect: NSRect(x: x, y: rect.minY + 6, width: 18, height: 5), xRadius: 2, yRadius: 2).fill()
            let latest = item.points.last?.value ?? 0
            let label = "\(item.group) \(latest >= 100 ? "\(Int(latest.rounded()))" : String(format: "%.1f", latest))"
            let size = (label as NSString).size(withAttributes: attrs)
            label.draw(in: NSRect(x: x + 24, y: rect.minY, width: size.width + 8, height: 18), withAttributes: attrs)
            x += 34 + size.width + 22
            if x > rect.maxX - 120 { break }
        }
    }

    private func drawTooltipIfNeeded(in plotRect: NSRect, minDate: Date, timeSpan: TimeInterval, minValue: Double, valueSpan: Double) {
        guard let hoverLocation, plotRect.contains(hoverLocation) else { return }
        let ratio = max(0, min((hoverLocation.x - plotRect.minX) / plotRect.width, 1))
        let targetDate = minDate.addingTimeInterval(TimeInterval(ratio) * timeSpan)
        let rows = tooltipRows(near: targetDate)
        guard !rows.isEmpty else { return }

        let nearestDate = rows.map(\.snapshot.date).min { abs($0.timeIntervalSince(targetDate)) < abs($1.timeIntervalSince(targetDate)) } ?? targetDate
        let x = plotRect.minX + CGFloat(nearestDate.timeIntervalSince(minDate) / timeSpan) * plotRect.width
        NSColor.secondaryLabelColor.withAlphaComponent(0.5).setStroke()
        let guide = NSBezierPath()
        guide.lineWidth = 1
        guide.move(to: NSPoint(x: x, y: plotRect.minY))
        guide.line(to: NSPoint(x: x, y: plotRect.maxY))
        guide.stroke()

        for row in rows {
            if let value = metric.value(in: row.snapshot) {
                let y = plotRect.maxY - CGFloat((value - minValue) / valueSpan) * plotRect.height
                row.color.setFill()
                NSBezierPath(ovalIn: NSRect(x: x - 4, y: y - 4, width: 8, height: 8)).fill()
            }
        }
        drawTooltipBox(rows: rows, anchor: NSPoint(x: x, y: hoverLocation.y), in: plotRect)
    }

    private func tooltipRows(near targetDate: Date) -> [(group: String, snapshot: PoolSnapshot, color: NSColor)] {
        let orderedGroups = groups.isEmpty
            ? Array(Set(history.map(\.groupName))).sorted()
            : groups
        return orderedGroups.enumerated().compactMap { index, group in
            let rows = history.filter { $0.groupName == group }
            guard let nearest = rows.min(by: {
                abs($0.date.timeIntervalSince(targetDate)) < abs($1.date.timeIntervalSince(targetDate))
            }) else { return nil }
            return (group, nearest, palette[index % palette.count])
        }
    }

    private func drawTooltipBox(rows: [(group: String, snapshot: PoolSnapshot, color: NSColor)], anchor: NSPoint, in plotRect: NSRect) {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "MM-dd HH:mm:ss"
        let title = formatter.string(from: rows.first?.snapshot.date ?? Date())
        let lines = rows.map { row -> String in
            let five = row.snapshot.remaining5h.map(String.init) ?? "--"
            let seven = row.snapshot.remaining7d.map(String.init) ?? "--"
            let fivePercent = row.snapshot.utilization5h.map { String(format: "%.1f%%", $0) } ?? "--"
            let sevenPercent = row.snapshot.utilization7d.map { String(format: "%.1f%%", $0) } ?? "--"
            return "\(row.group)  5h \(five) (\(fivePercent))   7d \(seven) (\(sevenPercent))"
        }
        let titleAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .bold),
            .foregroundColor: NSColor.labelColor
        ]
        let rowAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .medium),
            .foregroundColor: NSColor.labelColor
        ]
        let width = max((title as NSString).size(withAttributes: titleAttrs).width, lines.map { ($0 as NSString).size(withAttributes: rowAttrs).width }.max() ?? 0) + 28
        let height = CGFloat(28 + lines.count * 20)
        var x = anchor.x + 12
        if x + width > plotRect.maxX { x = anchor.x - width - 12 }
        let y = min(max(anchor.y - height / 2, plotRect.minY + 4), plotRect.maxY - height - 4)
        let rect = NSRect(x: x, y: y, width: width, height: height)

        NSColor.windowBackgroundColor.withAlphaComponent(0.96).setFill()
        NSBezierPath(roundedRect: rect, xRadius: 8, yRadius: 8).fill()
        NSColor.separatorColor.setStroke()
        NSBezierPath(roundedRect: rect, xRadius: 8, yRadius: 8).stroke()

        title.draw(in: NSRect(x: rect.minX + 12, y: rect.minY + 8, width: rect.width - 24, height: 16), withAttributes: titleAttrs)
        for (index, line) in lines.enumerated() {
            let rowY = rect.minY + 30 + CGFloat(index * 20)
            rows[index].color.setFill()
            NSBezierPath(ovalIn: NSRect(x: rect.minX + 12, y: rowY + 5, width: 7, height: 7)).fill()
            line.draw(in: NSRect(x: rect.minX + 24, y: rowY, width: rect.width - 34, height: 16), withAttributes: rowAttrs)
        }
    }

    private func drawEmpty(in rect: NSRect) {
        let text = "暂无轮询历史"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 16, weight: .semibold),
            .foregroundColor: NSColor.secondaryLabelColor
        ]
        let size = (text as NSString).size(withAttributes: attrs)
        text.draw(in: NSRect(x: rect.midX - size.width / 2, y: rect.midY - 10, width: size.width, height: 24), withAttributes: attrs)
    }
}

final class BalanceTrendChartView: NSView {
    var history: [Snapshot] = [] {
        didSet { needsDisplay = true }
    }

    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        NSColor.white.setFill()
        bounds.fill()
        let rect = bounds.insetBy(dx: 54, dy: 34)
        let points = history.sorted { $0.date < $1.date }.map { ($0.date, $0.total) }
        guard points.count >= 2, let minDate = points.map(\.0).min(), let maxDate = points.map(\.0).max() else {
            drawEmpty()
            return
        }
        let minValue = max((points.map(\.1).min() ?? 0) * 0.96, 0)
        let maxValue = max((points.map(\.1).max() ?? 1) * 1.04, minValue + 1)
        let timeSpan = max(maxDate.timeIntervalSince(minDate), 1)
        let valueSpan = max(maxValue - minValue, 1)
        drawGrid(in: rect, minValue: minValue, maxValue: maxValue)
        let path = NSBezierPath()
        path.lineWidth = 2.6
        path.lineJoinStyle = .round
        for (index, point) in points.enumerated() {
            let x = rect.minX + CGFloat(point.0.timeIntervalSince(minDate) / timeSpan) * rect.width
            let y = rect.maxY - CGFloat((point.1 - minValue) / valueSpan) * rect.height
            index == 0 ? path.move(to: NSPoint(x: x, y: y)) : path.line(to: NSPoint(x: x, y: y))
        }
        NSColor.systemGreen.setStroke()
        path.stroke()
        if let latest = points.last {
            let x = rect.minX + CGFloat(latest.0.timeIntervalSince(minDate) / timeSpan) * rect.width
            let y = rect.maxY - CGFloat((latest.1 - minValue) / valueSpan) * rect.height
            NSColor.systemGreen.setFill()
            NSBezierPath(ovalIn: NSRect(x: x - 4, y: y - 4, width: 8, height: 8)).fill()
        }
        drawTimeAxis(in: rect, minDate: minDate, maxDate: maxDate)
        drawLatest(points.last?.1 ?? 0, in: rect)
    }

    private func drawGrid(in rect: NSRect, minValue: Double, maxValue: Double) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .right
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11, weight: .medium),
            .foregroundColor: NSColor.secondaryLabelColor,
            .paragraphStyle: paragraph
        ]
        NSColor.separatorColor.withAlphaComponent(0.6).setStroke()
        for index in 0...3 {
            let ratio = CGFloat(index) / 3
            let y = rect.maxY - ratio * rect.height
            let path = NSBezierPath()
            path.move(to: NSPoint(x: rect.minX, y: y))
            path.line(to: NSPoint(x: rect.maxX, y: y))
            path.stroke()
            let value = minValue + Double(ratio) * (maxValue - minValue)
            String(format: "%.0f", value).draw(in: NSRect(x: 0, y: y - 8, width: rect.minX - 8, height: 16), withAttributes: attrs)
        }
    }

    private func drawLatest(_ value: Double, in rect: NSRect) {
        let text = "最新余额合计 \(String(format: "%.2f", value))"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 13, weight: .bold),
            .foregroundColor: NSColor.systemGreen
        ]
        text.draw(in: NSRect(x: rect.minX, y: 8, width: 240, height: 20), withAttributes: attrs)
    }

    private func drawEmpty() {
        let text = "查询最新余额后开始显示余额走势"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 14, weight: .semibold),
            .foregroundColor: NSColor.secondaryLabelColor
        ]
        let size = (text as NSString).size(withAttributes: attrs)
        text.draw(in: NSRect(x: bounds.midX - size.width / 2, y: bounds.midY - 10, width: size.width, height: 24), withAttributes: attrs)
    }
}

final class UsageBarView: NSView {
    var value: Double = 0 {
        didSet { needsDisplay = true }
    }

    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let clamped = max(0, min(value, 1))
        let track = bounds.insetBy(dx: 0, dy: max((bounds.height - 8) / 2, 0))
        let path = NSBezierPath(roundedRect: track, xRadius: 4, yRadius: 4)
        NSColor(calibratedWhite: 0.86, alpha: 1).setFill()
        path.fill()

        guard clamped > 0 else { return }
        let fillRect = NSRect(x: track.minX, y: track.minY, width: track.width * CGFloat(clamped), height: track.height)
        let fillPath = NSBezierPath(roundedRect: fillRect, xRadius: 4, yRadius: 4)
        usageColor(for: clamped).setFill()
        fillPath.fill()
    }

    private func usageColor(for value: Double) -> NSColor {
        let percent = value * 100
        if percent >= 100 { return .systemRed }
        if percent >= 80 { return .systemOrange }
        return .systemGreen
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate {
    private let updaterController = SPUStandardUpdaterController(
        startingUpdater: true,
        updaterDelegate: nil,
        userDriverDelegate: nil
    )
    private var window: NSWindow!
    private let costField = NSTextField(string: "200")
    private let partnerCostField = NSTextField(string: "0")
    private let withdrawalField = NSTextField(string: "")
    private let useBaseDeductionButton = NSButton(checkboxWithTitle: "扣基准余额", target: nil, action: nil)
    private let baseDeductionField = NSTextField(string: "")
    private let statusLabel = NSTextField(labelWithString: "")
    private let serverSyncButton = NSButton(title: "上传服务器", target: nil, action: nil)
    private var metricAnimations: [String: Timer] = [:]
    private var displayedMetricValues: [String: Double] = [:]
    private var poolAnimations: [String: Timer] = [:]
    private var displayedPoolValues: [String: Int] = [:]
    private var feedbackBar: NSView?
    private var feedbackHideTimer: Timer?
    private var pasteInProgress = false
    private var lastSuccessfulPasteAt: Date?
    private let pasteDebounceInterval: TimeInterval = 3

    private let costValue = NSTextField(labelWithString: "--")
    private let currentValue = NSTextField(labelWithString: "--")
    private let netValue = NSTextField(labelWithString: "--")
    private let resultValue = NSTextField(labelWithString: "--")
    private let remainingValue = NSTextField(labelWithString: "--")
    private let socialReceivableValue = NSTextField(labelWithString: "--")
    private let xingReceivableValue = NSTextField(labelWithString: "--")
    private let partnerNameField = NSTextField(string: "社会哥")
    private let comparisonTable = NSTableView()
    private let historyTable = NSTableView()
    private let plusPoolHistoryTable = NSTableView()
    private let k12PoolHistoryTable = NSTableView()
    private let poolGroupsLabel = NSTextField(labelWithString: "PLUS共享号池、K12共享号池")
    private let poolGroupsButton = NSButton(title: "选择号池", target: nil, action: nil)
    private let poolPollingField = NSTextField(string: "5")
    private let poolRefreshButton = NSButton(title: "刷新", target: nil, action: nil)
    private let poolCredentialsButton = NSButton(title: "接口账号", target: nil, action: nil)
    private let poolWarningButton = NSButton(title: "预警设置", target: nil, action: nil)
    private let tabControl = NSSegmentedControl(labels: ["趋势分析", "账号池分析", "成本计算", "成本历史"], trackingMode: .selectOne, target: nil, action: nil)
    private let trendMetricControl = NSSegmentedControl(labels: PoolTrendMetric.allCases.map(\.title), trackingMode: .selectOne, target: nil, action: nil)
    private let trendChartView = PoolTrendChartView()
    private let balanceTrendChartView = BalanceTrendChartView()
    private let contentHost = NSView()
    private var topPanel: NSView?
    private var metricWrap: NSView?
    private var activeContentView: NSView?
    private var poolSummaryLabels: [String: (total: NSTextField, schedulable: NSTextField, status: NSTextField, change: NSTextField, time: NSTextField)] = [:]
    private var poolHistoryTables: [NSTableView: String] = [:]
    private var settlementTextObservers: [NSObjectProtocol] = []
    private var settlementLabels: (balanceChange: NSTextField, netOutcome: NSTextField, settlementLine: NSTextField)?

    private var initial: Snapshot?
    private var withdrawalAmount: Double?
    private var useBaseDeduction = false
    private var baseDeductionAmount: Double?
    private var history: [Snapshot] = []
    private var costAdditions: [CostAddition] = []
    private var poolHistory: [PoolSnapshot] = []
    private var selectedPoolGroups: [String] = ["PLUS共享号池", "K12共享号池"]
    private var availablePoolGroups: [String] = ["PLUS共享号池", "K12共享号池"]
    private var hasLocalPoolSelection = false
    private var poolAccessToken: String?
    private var poolRefreshToken: String?
    private var poolRefreshInProgress = false
    private var poolPollingMinutes: Double = defaultPollingMinutes
    private var poolPollingTimer: Timer?
    private var selectedTrendMetric: PoolTrendMetric = .remaining5h
    private var smtpSettings = SMTPSettings.default
    private var poolWarningDedup: Set<String> = []
    private var balanceAccounts: [BalanceAccount] = []
    private var balancePollingTimer: Timer?
    private var balanceQueryInProgress = false
    private var serverSyncInProgress = false
    private var storedStateSyncTimer: Timer?
    private var serverInitialized = false {
        didSet {
            serverSyncButton.isHidden = serverInitialized
        }
    }
    private var settlement = SettlementState.default

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard !activateExistingInstanceIfNeeded() else { return }
        buildMenu()
        buildWindow()
        loadState()
        loadPoolState()
        balanceAccounts = loadBalanceAccounts()
        if UserDefaults.standard.bool(forKey: "ServerInitialized") {
            serverInitialized = true
        } else {
            serverSyncButton.isHidden = true
        }
        refreshOutput()
        restartPoolPollingTimer()
        restartBalancePollingTimer()
        fetchServerState(manual: false)
    }

    private func activateExistingInstanceIfNeeded() -> Bool {
        guard let bundleIdentifier = Bundle.main.bundleIdentifier else { return false }
        let currentPID = ProcessInfo.processInfo.processIdentifier
        let existing = NSRunningApplication
            .runningApplications(withBundleIdentifier: bundleIdentifier)
            .first { $0.processIdentifier != currentPID && !$0.isTerminated }
        guard let existing else { return false }
        existing.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
        NSApp.terminate(nil)
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        clearSettlementTextObservers()
        feedbackHideTimer?.invalidate()
        poolPollingTimer?.invalidate()
        balancePollingTimer?.invalidate()
        storedStateSyncTimer?.invalidate()
    }

    private func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "GPT分析器"
        window.center()

        configureField(costField, placeholder: "200")
        configureField(partnerCostField, placeholder: "0")
        configureField(withdrawalField, placeholder: "2300")
        configureField(baseDeductionField, placeholder: "128.70")
        baseDeductionField.isHidden = true
        useBaseDeductionButton.target = self
        useBaseDeductionButton.action = #selector(toggleBaseDeduction)
        useBaseDeductionButton.state = .off

        let initialButton = button("账号配置", action: #selector(editBalanceAccounts))
        let queryLatestButton = button("查询最新余额", action: #selector(queryBalanceAsLatest))
        let resetButton = button("一键重置", action: #selector(resetAll))
        let addCostButton = button("累加成本", action: #selector(addCost))
        let costAdditionHistoryButton = button("累加历史", action: #selector(showCostAdditionHistory))
        serverSyncButton.target = self
        serverSyncButton.action = #selector(uploadInitialStateToServer)
        serverSyncButton.bezelStyle = .rounded
        serverSyncButton.controlSize = .large
        serverSyncButton.font = .systemFont(ofSize: 13, weight: .semibold)
        serverSyncButton.isHidden = true
        serverSyncButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 120).isActive = true
        let buttonRow = NSStackView(views: [initialButton, queryLatestButton, resetButton, serverSyncButton])
        buttonRow.orientation = .horizontal
        buttonRow.spacing = 10
        buttonRow.alignment = .centerY

        statusLabel.textColor = .secondaryLabelColor
        statusLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        statusLabel.alignment = .left
        statusLabel.lineBreakMode = .byWordWrapping

        let root = NSStackView()
        root.orientation = .vertical
        root.spacing = 0
        root.alignment = .width
        root.translatesAutoresizingMaskIntoConstraints = false

        let costTopPanel = buildTopPanel(
            buttonRow: buttonRow,
            addCostButton: addCostButton,
            costAdditionHistoryButton: costAdditionHistoryButton
        )
        topPanel = costTopPanel
        let tabBar = buildTabBar()
        let feedback = buildFeedbackBar()
        feedbackBar = feedback
        let metricRow = buildMetricRow()
        let costMetricWrap = NSView()
        costMetricWrap.translatesAutoresizingMaskIntoConstraints = false
        costMetricWrap.addSubview(metricRow)
        metricWrap = costMetricWrap
        NSLayoutConstraint.activate([
            metricRow.leadingAnchor.constraint(equalTo: costMetricWrap.leadingAnchor, constant: 20),
            metricRow.trailingAnchor.constraint(equalTo: costMetricWrap.trailingAnchor, constant: -20),
            metricRow.topAnchor.constraint(equalTo: costMetricWrap.topAnchor, constant: 14),
            metricRow.bottomAnchor.constraint(equalTo: costMetricWrap.bottomAnchor, constant: -12)
        ])

        contentHost.translatesAutoresizingMaskIntoConstraints = false
        contentHost.setContentHuggingPriority(.defaultLow, for: .vertical)
        contentHost.setContentCompressionResistancePriority(.defaultLow, for: .vertical)

        root.addArrangedSubview(tabBar)
        root.addArrangedSubview(costTopPanel)
        root.addArrangedSubview(costMetricWrap)
        root.addArrangedSubview(contentHost)
        NSLayoutConstraint.activate([
            costTopPanel.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            costTopPanel.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            tabBar.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            tabBar.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            costMetricWrap.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            costMetricWrap.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            contentHost.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            contentHost.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            contentHost.heightAnchor.constraint(greaterThanOrEqualToConstant: 380)
        ])

        let content = NSView()
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.white.cgColor
        content.addSubview(root)
        content.addSubview(feedback)
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            root.topAnchor.constraint(equalTo: content.topAnchor),
            root.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            feedback.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            feedback.topAnchor.constraint(equalTo: content.topAnchor, constant: 42),
            feedback.widthAnchor.constraint(greaterThanOrEqualToConstant: 260),
            feedback.widthAnchor.constraint(lessThanOrEqualTo: content.widthAnchor, constant: -80)
        ])

        window.contentView = content
        switchTab(nil)
        window.makeKeyAndOrderFront(nil)

        NotificationCenter.default.addObserver(self, selector: #selector(inputsChanged), name: NSControl.textDidChangeNotification, object: costField)
        NotificationCenter.default.addObserver(self, selector: #selector(inputsChanged), name: NSControl.textDidChangeNotification, object: partnerCostField)
        NotificationCenter.default.addObserver(self, selector: #selector(inputsChanged), name: NSControl.textDidChangeNotification, object: withdrawalField)
        NotificationCenter.default.addObserver(self, selector: #selector(inputsChanged), name: NSControl.textDidChangeNotification, object: baseDeductionField)

    }

    private func isEditingTextInput() -> Bool {
        guard let responder = window?.firstResponder else { return false }
        if responder is NSTextView {
            return true
        }
        if let view = responder as? NSView {
            var current: NSView? = view
            while let item = current {
                if item is NSTextField {
                    return true
                }
                current = item.superview
            }
        }
        return false
    }

    private func buildMenu() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)

        let appMenu = NSMenu()
        appItem.submenu = appMenu

        let checkForUpdatesItem = NSMenuItem(
            title: "检查更新…",
            action: #selector(SPUStandardUpdaterController.checkForUpdates(_:)),
            keyEquivalent: ""
        )
        checkForUpdatesItem.target = updaterController
        appMenu.addItem(checkForUpdatesItem)
        appMenu.addItem(.separator())

        let quitItem = NSMenuItem(
            title: "退出 GPT分析器",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        quitItem.keyEquivalentModifierMask = [.command]
        appMenu.addItem(quitItem)

        let editItem = NSMenuItem()
        mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "编辑")
        editItem.submenu = editMenu
        editMenu.addItem(NSMenuItem(title: "撤销", action: Selector(("undo:")), keyEquivalent: "z"))
        let redoItem = NSMenuItem(title: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        redoItem.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(redoItem)
        editMenu.addItem(.separator())
        editMenu.addItem(NSMenuItem(title: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))

        NSApplication.shared.mainMenu = mainMenu
    }


    private func configureField(_ field: NSTextField, placeholder: String) {
        field.placeholderString = placeholder
        field.alignment = .right
        field.font = .boldSystemFont(ofSize: 15)
        field.translatesAutoresizingMaskIntoConstraints = false
        field.widthAnchor.constraint(equalToConstant: 96).isActive = true
        field.heightAnchor.constraint(equalToConstant: 26).isActive = true
    }

    private func label(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: 13, weight: .semibold)
        label.textColor = .secondaryLabelColor
        return label
    }

    private func button(_ title: String, action: Selector) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.bezelStyle = .rounded
        button.controlSize = .large
        button.font = .systemFont(ofSize: 13, weight: .semibold)
        button.widthAnchor.constraint(greaterThanOrEqualToConstant: title == "一键重置" ? 80 : 120).isActive = true
        return button
    }

    private func buildTopPanel(
        buttonRow: NSStackView,
        addCostButton: NSButton,
        costAdditionHistoryButton: NSButton
    ) -> NSView {
        let panel = NSView()
        panel.translatesAutoresizingMaskIntoConstraints = false

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 8
        stack.alignment = .leading
        stack.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)
        stack.translatesAutoresizingMaskIntoConstraints = false

        let inputRow = NSStackView()
        inputRow.orientation = .horizontal
        inputRow.spacing = 8
        inputRow.alignment = .centerY
        inputRow.translatesAutoresizingMaskIntoConstraints = false
        inputRow.addArrangedSubview(label("星星出资"))
        inputRow.addArrangedSubview(costField)
        inputRow.addArrangedSubview(label("社会哥出资"))
        inputRow.addArrangedSubview(partnerCostField)
        inputRow.addArrangedSubview(label("本次提现金额"))
        inputRow.addArrangedSubview(withdrawalField)
        inputRow.addArrangedSubview(useBaseDeductionButton)
        inputRow.addArrangedSubview(baseDeductionField)
        inputRow.addArrangedSubview(addCostButton)
        inputRow.addArrangedSubview(costAdditionHistoryButton)
        let actionRow = NSStackView()
        actionRow.orientation = .horizontal
        actionRow.spacing = 8
        actionRow.alignment = .centerY
        actionRow.translatesAutoresizingMaskIntoConstraints = false
        for button in Array(buttonRow.arrangedSubviews) {
            buttonRow.removeArrangedSubview(button)
            actionRow.addArrangedSubview(button)
        }

        let inputScroll = horizontalScroll(documentView: inputRow)
        let actionScroll = horizontalScroll(documentView: actionRow)
        stack.addArrangedSubview(inputScroll)
        stack.addArrangedSubview(actionScroll)

        panel.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: panel.trailingAnchor),
            stack.topAnchor.constraint(equalTo: panel.topAnchor),
            stack.bottomAnchor.constraint(equalTo: panel.bottomAnchor),
            inputScroll.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            inputScroll.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            inputScroll.heightAnchor.constraint(equalToConstant: 30),
            actionScroll.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            actionScroll.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            actionScroll.heightAnchor.constraint(equalToConstant: 32),
            inputRow.leadingAnchor.constraint(equalTo: inputScroll.contentView.leadingAnchor),
            inputRow.topAnchor.constraint(equalTo: inputScroll.contentView.topAnchor),
            inputRow.bottomAnchor.constraint(equalTo: inputScroll.contentView.bottomAnchor),
            actionRow.leadingAnchor.constraint(equalTo: actionScroll.contentView.leadingAnchor),
            actionRow.topAnchor.constraint(equalTo: actionScroll.contentView.topAnchor),
            actionRow.bottomAnchor.constraint(equalTo: actionScroll.contentView.bottomAnchor),
            panel.heightAnchor.constraint(equalToConstant: 86)
        ])
        return panel
    }

    private func horizontalScroll(documentView: NSView) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = documentView
        scroll.hasHorizontalScroller = true
        scroll.hasVerticalScroller = false
        scroll.autohidesScrollers = true
        scroll.borderType = .noBorder
        return scroll
    }

    private func buildTabBar() -> NSView {
        let bar = NSView()
        bar.wantsLayer = true
        bar.layer?.backgroundColor = NSColor(calibratedWhite: 0.95, alpha: 1).cgColor
        bar.translatesAutoresizingMaskIntoConstraints = false

        tabControl.target = self
        tabControl.action = #selector(switchTab(_:))
        tabControl.selectedSegment = 0
        tabControl.segmentStyle = .rounded
        tabControl.controlSize = .large
        tabControl.setWidth(100, forSegment: 0)
        tabControl.setWidth(112, forSegment: 1)
        tabControl.setWidth(92, forSegment: 2)
        tabControl.setWidth(92, forSegment: 3)
        tabControl.translatesAutoresizingMaskIntoConstraints = false

        bar.addSubview(tabControl)
        NSLayoutConstraint.activate([
            bar.heightAnchor.constraint(equalToConstant: 32),
            tabControl.centerXAnchor.constraint(equalTo: bar.centerXAnchor),
            tabControl.centerYAnchor.constraint(equalTo: bar.centerYAnchor)
        ])
        return bar
    }

    private func buildFeedbackBar() -> NSView {
        let bar = NSView()
        bar.wantsLayer = true
        bar.layer?.backgroundColor = NSColor(calibratedWhite: 0.08, alpha: 0.92).cgColor
        bar.layer?.cornerRadius = 10
        bar.layer?.shadowColor = NSColor.black.cgColor
        bar.layer?.shadowOpacity = 0.18
        bar.layer?.shadowOffset = NSSize(width: 0, height: -2)
        bar.layer?.shadowRadius = 8
        bar.translatesAutoresizingMaskIntoConstraints = false
        bar.isHidden = true

        statusLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        statusLabel.alignment = .center
        statusLabel.maximumNumberOfLines = 2
        statusLabel.lineBreakMode = .byWordWrapping
        statusLabel.translatesAutoresizingMaskIntoConstraints = false

        bar.addSubview(statusLabel)
        NSLayoutConstraint.activate([
            bar.heightAnchor.constraint(greaterThanOrEqualToConstant: 36),
            statusLabel.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 18),
            statusLabel.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -18),
            statusLabel.centerYAnchor.constraint(equalTo: bar.centerYAnchor)
        ])
        return bar
    }

    private func showFeedback(_ message: String, color: NSColor = .white, autoHide: Bool = true) {
        feedbackHideTimer?.invalidate()
        let summary = message.count > 120 ? String(message.prefix(120)) + "..." : message
        statusLabel.stringValue = summary
        statusLabel.textColor = color
        feedbackBar?.isHidden = false
        if message.count > 180 || message.contains("\n") {
            showDetailDialog(title: "提示详情", message: message, style: color == .systemRed ? .warning : .informational)
        }
        guard autoHide else { return }
        feedbackHideTimer = Timer.scheduledTimer(withTimeInterval: 3.2, repeats: false) { [weak self] _ in
            self?.feedbackBar?.isHidden = true
        }
    }

    private func beginPasteIfAllowed() -> Bool {
        if pasteInProgress {
            showFeedback("正在识别上一张截图，已忽略重复粘贴。", color: .systemOrange)
            return false
        }
        if let lastSuccessfulPasteAt {
            let elapsed = Date().timeIntervalSince(lastSuccessfulPasteAt)
            if elapsed < pasteDebounceInterval {
                let remaining = max(1, Int(ceil(pasteDebounceInterval - elapsed)))
                showFeedback("\(remaining) 秒后再粘贴，已防止重复记录。", color: .systemOrange)
                return false
            }
        }
        pasteInProgress = true
        showFeedback("正在识别截图...", color: .white, autoHide: false)
        return true
    }

    private func finishPaste(success: Bool) {
        pasteInProgress = false
        if success {
            lastSuccessfulPasteAt = Date()
        }
    }

    @objc private func switchTab(_ sender: Any?) {
        activeContentView?.removeFromSuperview()
        let isAccountTab = tabControl.selectedSegment == 0 || tabControl.selectedSegment == 1
        topPanel?.isHidden = isAccountTab
        metricWrap?.isHidden = isAccountTab
        let nextView: NSView
        switch tabControl.selectedSegment {
        case 1:
            nextView = buildPoolPanel()
        case 2:
            nextView = buildCalcPanel()
        case 3:
            nextView = buildHistoryPanel()
        default:
            nextView = buildTrendPanel()
        }
        activeContentView = nextView
        nextView.translatesAutoresizingMaskIntoConstraints = false
        contentHost.addSubview(nextView)
        NSLayoutConstraint.activate([
            nextView.leadingAnchor.constraint(equalTo: contentHost.leadingAnchor),
            nextView.trailingAnchor.constraint(equalTo: contentHost.trailingAnchor),
            nextView.topAnchor.constraint(equalTo: contentHost.topAnchor),
            nextView.bottomAnchor.constraint(equalTo: contentHost.bottomAnchor)
        ])
        scrollToLatestRows()
    }

    private func buildPoolPanel() -> NSView {
        let panel = NSView()
        panel.wantsLayer = true
        panel.layer?.backgroundColor = NSColor.white.cgColor
        poolHistoryTables = [:]

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 10
        stack.alignment = .width
        stack.translatesAutoresizingMaskIntoConstraints = false

        let toolbar = buildPoolToolbar()
        toolbar.heightAnchor.constraint(equalToConstant: 34).isActive = true

        let sectionStack = NSStackView()
        sectionStack.orientation = .vertical
        sectionStack.spacing = 10
        sectionStack.alignment = .width
        sectionStack.translatesAutoresizingMaskIntoConstraints = false

        let groups = selectedPoolGroups.isEmpty ? ["PLUS共享号池", "K12共享号池"] : selectedPoolGroups
        var firstSection: NSView?
        for group in groups {
            let table = NSTableView()
            poolHistoryTables[table] = group
            let section = poolHistorySection(title: "\(group)历史", groupName: group, table: table)
            section.setContentHuggingPriority(.defaultLow, for: .vertical)
            section.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
            sectionStack.addArrangedSubview(section)
            section.leadingAnchor.constraint(equalTo: sectionStack.leadingAnchor).isActive = true
            section.trailingAnchor.constraint(equalTo: sectionStack.trailingAnchor).isActive = true
            if let firstSection {
                section.heightAnchor.constraint(equalTo: firstSection.heightAnchor).isActive = true
            } else {
                firstSection = section
            }
        }

        let sectionScroll = NSScrollView()
        sectionScroll.translatesAutoresizingMaskIntoConstraints = false
        sectionScroll.documentView = sectionStack
        sectionScroll.hasVerticalScroller = true
        sectionScroll.hasHorizontalScroller = false
        sectionScroll.autohidesScrollers = true
        sectionScroll.borderType = .noBorder
        sectionScroll.setContentHuggingPriority(.defaultLow, for: .vertical)
        sectionScroll.setContentCompressionResistancePriority(.defaultLow, for: .vertical)

        stack.addArrangedSubview(toolbar)
        stack.addArrangedSubview(sectionScroll)
        NSLayoutConstraint.activate([
            toolbar.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            toolbar.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            sectionScroll.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            sectionScroll.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            sectionStack.leadingAnchor.constraint(equalTo: sectionScroll.contentView.leadingAnchor),
            sectionStack.trailingAnchor.constraint(equalTo: sectionScroll.contentView.trailingAnchor),
            sectionStack.topAnchor.constraint(equalTo: sectionScroll.contentView.topAnchor),
            sectionStack.widthAnchor.constraint(equalTo: sectionScroll.contentView.widthAnchor)
        ])
        if groups.count <= 2 {
            firstSection?.heightAnchor.constraint(greaterThanOrEqualTo: sectionScroll.heightAnchor, multiplier: groups.count == 1 ? 1 : 0.49).isActive = true
        } else {
            firstSection?.heightAnchor.constraint(equalToConstant: 260).isActive = true
        }

        panel.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: panel.topAnchor, constant: 10),
            stack.bottomAnchor.constraint(equalTo: panel.bottomAnchor, constant: -12)
        ])
        return panel
    }

    private func buildTrendPanel() -> NSView {
        let panel = NSView()
        panel.wantsLayer = true
        panel.layer?.backgroundColor = NSColor.white.cgColor

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 12
        stack.alignment = .width
        stack.translatesAutoresizingMaskIntoConstraints = false

        let toolbar = NSStackView()
        toolbar.orientation = .horizontal
        toolbar.spacing = 10
        toolbar.alignment = .centerY

        let title = NSTextField(labelWithString: "账号池趋势")
        title.font = .systemFont(ofSize: 16, weight: .bold)
        title.textColor = .labelColor

        trendMetricControl.target = self
        trendMetricControl.action = #selector(trendMetricChanged)
        trendMetricControl.selectedSegment = selectedTrendMetric.rawValue
        trendMetricControl.segmentStyle = .rounded
        trendMetricControl.controlSize = .regular
        for index in 0..<PoolTrendMetric.allCases.count {
            trendMetricControl.setWidth(index < 2 ? 86 : 74, forSegment: index)
        }

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        toolbar.addArrangedSubview(title)
        toolbar.addArrangedSubview(spacer)
        toolbar.addArrangedSubview(trendMetricControl)

        let hint = NSTextField(labelWithString: "每次手动刷新或自动轮询都会追加历史；10 分钟内某分组总账号减少超过 100 会触发预警。")
        hint.font = .systemFont(ofSize: 12, weight: .medium)
        hint.textColor = .secondaryLabelColor
        hint.maximumNumberOfLines = 1
        hint.lineBreakMode = .byTruncatingTail

        trendChartView.history = poolHistory
        trendChartView.groups = selectedPoolGroups
        trendChartView.metric = selectedTrendMetric
        trendChartView.translatesAutoresizingMaskIntoConstraints = false
        trendChartView.wantsLayer = true
        trendChartView.layer?.borderColor = NSColor.separatorColor.cgColor
        trendChartView.layer?.borderWidth = 1
        trendChartView.layer?.cornerRadius = 8

        stack.addArrangedSubview(toolbar)
        stack.addArrangedSubview(hint)
        stack.addArrangedSubview(trendChartView)
        NSLayoutConstraint.activate([
            trendChartView.heightAnchor.constraint(greaterThanOrEqualToConstant: 520)
        ])

        panel.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: panel.topAnchor, constant: 14),
            stack.bottomAnchor.constraint(equalTo: panel.bottomAnchor, constant: -16)
        ])
        return panel
    }

    @objc private func trendMetricChanged() {
        selectedTrendMetric = PoolTrendMetric(rawValue: trendMetricControl.selectedSegment) ?? .remaining5h
        trendChartView.metric = selectedTrendMetric
    }

    private func buildPoolToolbar() -> NSView {
        let row = NSStackView()
        row.orientation = .horizontal
        row.spacing = 8
        row.alignment = .centerY
        row.translatesAutoresizingMaskIntoConstraints = false

        updatePoolGroupsLabel()
        poolGroupsLabel.font = .systemFont(ofSize: 13, weight: .medium)
        poolGroupsLabel.textColor = .labelColor
        poolGroupsLabel.lineBreakMode = .byTruncatingTail
        poolGroupsLabel.maximumNumberOfLines = 1
        poolGroupsLabel.translatesAutoresizingMaskIntoConstraints = false
        poolGroupsLabel.widthAnchor.constraint(greaterThanOrEqualToConstant: 260).isActive = true

        poolGroupsButton.target = self
        poolGroupsButton.action = #selector(selectPoolGroups)
        poolGroupsButton.bezelStyle = .rounded
        poolGroupsButton.controlSize = .large
        poolGroupsButton.font = .systemFont(ofSize: 13, weight: .semibold)
        poolGroupsButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 96).isActive = true

        poolPollingField.stringValue = money(poolPollingMinutes)
        poolPollingField.placeholderString = "2"
        poolPollingField.alignment = .center
        poolPollingField.font = .systemFont(ofSize: 13, weight: .semibold)
        poolPollingField.cell?.usesSingleLineMode = true
        poolPollingField.translatesAutoresizingMaskIntoConstraints = false
        poolPollingField.widthAnchor.constraint(equalToConstant: 54).isActive = true
        poolPollingField.heightAnchor.constraint(equalToConstant: 28).isActive = true
        poolPollingField.target = self
        poolPollingField.action = #selector(poolPollingChanged)
        NotificationCenter.default.removeObserver(self, name: NSControl.textDidChangeNotification, object: poolPollingField)
        NotificationCenter.default.addObserver(self, selector: #selector(poolPollingChanged), name: NSControl.textDidChangeNotification, object: poolPollingField)

        poolRefreshButton.target = self
        poolRefreshButton.action = #selector(refreshPoolsFromAPIButton)
        poolRefreshButton.bezelStyle = .rounded
        poolRefreshButton.controlSize = .large
        poolRefreshButton.font = .systemFont(ofSize: 13, weight: .semibold)
        poolRefreshButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 86).isActive = true

        poolCredentialsButton.target = self
        poolCredentialsButton.action = #selector(editPoolCredentials)
        poolCredentialsButton.bezelStyle = .rounded
        poolCredentialsButton.controlSize = .large
        poolCredentialsButton.font = .systemFont(ofSize: 13, weight: .semibold)
        poolCredentialsButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 96).isActive = true

        poolWarningButton.target = self
        poolWarningButton.action = #selector(editWarningSettings)
        poolWarningButton.bezelStyle = .rounded
        poolWarningButton.controlSize = .large
        poolWarningButton.font = .systemFont(ofSize: 13, weight: .semibold)
        poolWarningButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 96).isActive = true

        let info = NSTextField(labelWithString: "平台共享容量池")
        info.font = .systemFont(ofSize: 13, weight: .semibold)
        info.textColor = .secondaryLabelColor

        let left = NSStackView()
        left.orientation = .horizontal
        left.spacing = 8
        left.alignment = .centerY
        left.addArrangedSubview(info)
        left.addArrangedSubview(poolGroupsLabel)
        left.addArrangedSubview(poolGroupsButton)

        let right = NSStackView()
        right.orientation = .horizontal
        right.spacing = 8
        right.alignment = .centerY
        right.addArrangedSubview(label("轮询"))
        right.addArrangedSubview(poolPollingField)
        right.addArrangedSubview(label("分钟"))
        right.addArrangedSubview(poolRefreshButton)
        right.addArrangedSubview(poolCredentialsButton)
        right.addArrangedSubview(poolWarningButton)

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        row.addArrangedSubview(left)
        row.addArrangedSubview(spacer)
        row.addArrangedSubview(right)
        return row
    }

    private func poolHistorySection(title: String, groupName: String, table: NSTableView) -> NSView {
        let section = NSStackView()
        section.orientation = .vertical
        section.spacing = 6
        section.alignment = .width
        section.translatesAutoresizingMaskIntoConstraints = false

        let label = NSTextField(labelWithString: title)
        label.font = .systemFont(ofSize: 13, weight: .bold)
        label.textColor = .secondaryLabelColor

        let clearButton = button("一键清空", action: #selector(confirmClearPoolHistory(_:)))
        clearButton.controlSize = .regular
        clearButton.font = .systemFont(ofSize: 12, weight: .semibold)
        clearButton.identifier = NSUserInterfaceItemIdentifier(groupName)
        clearButton.toolTip = "清理\(title)"

        let header = NSStackView()
        header.orientation = .horizontal
        header.spacing = 8
        header.alignment = .centerY
        let headerSpacer = NSView()
        headerSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        header.addArrangedSubview(label)
        header.addArrangedSubview(headerSpacer)
        header.addArrangedSubview(clearButton)

        let scroll = makeTableScroll(
            table: table,
            columns: [
                ("时间", "time", 118),
                ("总账号", "total", 88),
                ("5h窗口", "remaining5h", 178),
                ("7d窗口", "remaining7d", 178),
                ("并发可用", "concurrent", 138),
                ("错误", "error", 82),
                ("较上次变化", "delta", 84)
            ]
        )
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 170).isActive = true
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)
        scroll.setContentCompressionResistancePriority(.defaultLow, for: .vertical)

        section.addArrangedSubview(header)
        section.addArrangedSubview(scroll)
        NSLayoutConstraint.activate([
            header.leadingAnchor.constraint(equalTo: section.leadingAnchor),
            header.trailingAnchor.constraint(equalTo: section.trailingAnchor),
            scroll.leadingAnchor.constraint(equalTo: section.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: section.trailingAnchor)
        ])
        return section
    }

    private func poolSummaryCard(groupName: String, accent: NSColor) -> NSView {
        let card = NSView()
        card.wantsLayer = true
        card.layer?.cornerRadius = 8
        card.layer?.backgroundColor = accent.withAlphaComponent(0.08).cgColor
        card.layer?.borderColor = accent.withAlphaComponent(0.25).cgColor
        card.layer?.borderWidth = 1

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 8
        stack.alignment = .width
        stack.edgeInsets = NSEdgeInsets(top: 12, left: 16, bottom: 12, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false

        let name = NSTextField(labelWithString: groupName)
        name.font = .systemFont(ofSize: 17, weight: .bold)
        name.textColor = .labelColor

        let total = NSTextField(labelWithString: "总账号 --")
        total.font = .systemFont(ofSize: 20, weight: .bold)
        total.textColor = accent

        let schedulable = NSTextField(labelWithString: "可调度 --")
        schedulable.font = .systemFont(ofSize: 13, weight: .semibold)
        schedulable.textColor = .secondaryLabelColor

        let status = NSTextField(labelWithString: "状态 --")
        status.font = .systemFont(ofSize: 13, weight: .semibold)
        status.textColor = .secondaryLabelColor

        let change = NSTextField(labelWithString: "暂无对比")
        change.font = .systemFont(ofSize: 15, weight: .bold)
        change.textColor = .secondaryLabelColor

        let time = NSTextField(labelWithString: "暂无历史")
        time.font = .systemFont(ofSize: 12, weight: .medium)
        time.textColor = .tertiaryLabelColor

        for label in [name, total, schedulable, status, change, time] {
            label.maximumNumberOfLines = 1
            label.lineBreakMode = .byTruncatingTail
            label.setContentCompressionResistancePriority(.required, for: .vertical)
        }
        name.setContentCompressionResistancePriority(.required, for: .horizontal)
        total.setContentCompressionResistancePriority(.required, for: .horizontal)
        change.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        time.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        let topRow = NSStackView()
        topRow.orientation = .horizontal
        topRow.spacing = 10
        topRow.alignment = .centerY
        let topSpacer = NSView()
        topSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        topRow.addArrangedSubview(name)
        topRow.addArrangedSubview(topSpacer)
        topRow.addArrangedSubview(time)
        topRow.heightAnchor.constraint(equalToConstant: 28).isActive = true

        let valueRow = NSStackView()
        valueRow.orientation = .horizontal
        valueRow.spacing = 12
        valueRow.alignment = .centerY
        let valueSpacer = NSView()
        valueSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        valueRow.addArrangedSubview(total)
        valueRow.addArrangedSubview(valueSpacer)
        valueRow.addArrangedSubview(change)
        valueRow.heightAnchor.constraint(equalToConstant: 34).isActive = true

        schedulable.heightAnchor.constraint(equalToConstant: 20).isActive = true
        status.heightAnchor.constraint(equalToConstant: 20).isActive = true

        stack.addArrangedSubview(topRow)
        stack.addArrangedSubview(valueRow)
        stack.addArrangedSubview(schedulable)
        stack.addArrangedSubview(status)
        card.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: card.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: card.trailingAnchor),
            stack.topAnchor.constraint(equalTo: card.topAnchor),
            stack.bottomAnchor.constraint(equalTo: card.bottomAnchor),
            card.heightAnchor.constraint(equalToConstant: 164)
        ])
        poolSummaryLabels[groupName] = (total: total, schedulable: schedulable, status: status, change: change, time: time)
        return card
    }

    private func buildCalcPanel() -> NSView {
        let panel = NSView()
        panel.wantsLayer = true
        panel.layer?.backgroundColor = NSColor.white.cgColor

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 8
        stack.alignment = .width
        stack.translatesAutoresizingMaskIntoConstraints = false

        let comparisonScroll = makeTableScroll(
            table: comparisonTable,
            columns: [
                ("序号", "index", 90),
                ("账号", "account", 360),
                ("当前余额", "current", 180)
            ]
        )
        comparisonScroll.setContentHuggingPriority(.defaultLow, for: .horizontal)
        comparisonScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 250).isActive = true

        balanceTrendChartView.history = history
        balanceTrendChartView.translatesAutoresizingMaskIntoConstraints = false
        balanceTrendChartView.wantsLayer = true
        balanceTrendChartView.layer?.borderColor = NSColor.separatorColor.cgColor
        balanceTrendChartView.layer?.borderWidth = 1
        balanceTrendChartView.layer?.cornerRadius = 8
        balanceTrendChartView.heightAnchor.constraint(equalToConstant: 180).isActive = true

        let settlementPanel = buildSettlementPanel()
        stack.addArrangedSubview(balanceTrendChartView)
        stack.addArrangedSubview(settlementPanel)
        stack.addArrangedSubview(comparisonScroll)
        NSLayoutConstraint.activate([
            balanceTrendChartView.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            balanceTrendChartView.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            settlementPanel.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            settlementPanel.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            comparisonScroll.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            comparisonScroll.trailingAnchor.constraint(equalTo: stack.trailingAnchor)
        ])

        panel.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: panel.topAnchor, constant: 8),
            stack.bottomAnchor.constraint(equalTo: panel.bottomAnchor, constant: -16)
        ])
        return panel
    }

    private func buildSettlementPanel() -> NSView {
        clearSettlementTextObservers()

        let panel = NSView()
        panel.wantsLayer = true
        panel.layer?.backgroundColor = NSColor.systemIndigo.withAlphaComponent(0.06).cgColor
        panel.layer?.borderColor = NSColor.systemIndigo.withAlphaComponent(0.26).cgColor
        panel.layer?.borderWidth = 1
        panel.layer?.cornerRadius = 8
        panel.translatesAutoresizingMaskIntoConstraints = false

        let title = NSTextField(labelWithString: "合伙结算")
        title.font = .systemFont(ofSize: 20, weight: .bold)
        title.textColor = .systemIndigo
        title.alignment = .left
        title.translatesAutoresizingMaskIntoConstraints = false

        let hint = NSTextField(labelWithString: "按本次实际提现金额结算；扣成本后算当前提现利润；社会哥固定分成 40%。")
        hint.font = .systemFont(ofSize: 14, weight: .semibold)
        hint.textColor = .secondaryLabelColor
        hint.lineBreakMode = .byWordWrapping
        hint.maximumNumberOfLines = 2
        hint.alignment = .left
        hint.translatesAutoresizingMaskIntoConstraints = false

        let titleRow = NSStackView()
        titleRow.orientation = .horizontal
        titleRow.spacing = 28
        titleRow.alignment = .firstBaseline
        titleRow.translatesAutoresizingMaskIntoConstraints = false
        titleRow.addArrangedSubview(title)
        titleRow.addArrangedSubview(hint)
        title.widthAnchor.constraint(equalToConstant: 150).isActive = true

        let settingsRow = NSStackView()
        settingsRow.orientation = .horizontal
        settingsRow.spacing = 8
        settingsRow.alignment = .centerY
        settingsRow.translatesAutoresizingMaskIntoConstraints = false
        settingsRow.addArrangedSubview(label("社会哥分成 40%"))
        let ownerShare = NSTextField(labelWithString: "星星分成 60%")
        ownerShare.font = .systemFont(ofSize: 12, weight: .semibold)
        ownerShare.textColor = .secondaryLabelColor
        ownerShare.alignment = .left
        settingsRow.addArrangedSubview(ownerShare)

        let balanceChangeFormula = settlementFormulaValue()
        let netOutcomeFormula = settlementFormulaValue()
        let settlementLineFormula = settlementFormulaValue(isResult: true)
        let formulaGrid = settlementFormulaGrid(
            balanceChange: balanceChangeFormula,
            netOutcome: netOutcomeFormula,
            settlementLine: settlementLineFormula
        )

        panel.addSubview(titleRow)
        panel.addSubview(settingsRow)
        panel.addSubview(formulaGrid)
        NSLayoutConstraint.activate([
            titleRow.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 32),
            titleRow.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -32),
            titleRow.topAnchor.constraint(equalTo: panel.topAnchor, constant: 18),
            settingsRow.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 32),
            settingsRow.trailingAnchor.constraint(lessThanOrEqualTo: panel.trailingAnchor, constant: -32),
            settingsRow.topAnchor.constraint(equalTo: titleRow.bottomAnchor, constant: 14),
            formulaGrid.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 32),
            formulaGrid.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -32),
            formulaGrid.topAnchor.constraint(equalTo: settingsRow.bottomAnchor, constant: 16),
            formulaGrid.bottomAnchor.constraint(equalTo: panel.bottomAnchor, constant: -18)
        ])

        settlementLabels = (balanceChangeFormula, netOutcomeFormula, settlementLineFormula)
        updateSettlementOutput()
        return panel
    }

    private func settlementFormulaValue(isResult: Bool = false) -> NSTextField {
        let value = NSTextField(labelWithString: "")
        value.font = isResult ? .systemFont(ofSize: 13, weight: .bold) : .monospacedSystemFont(ofSize: 13, weight: .medium)
        value.textColor = .labelColor
        value.maximumNumberOfLines = 1
        value.lineBreakMode = .byClipping
        value.alignment = .left
        value.translatesAutoresizingMaskIntoConstraints = false
        value.setContentHuggingPriority(.defaultLow, for: .horizontal)
        value.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return value
    }

    private func settlementFormulaGrid(balanceChange: NSTextField, netOutcome: NSTextField, settlementLine: NSTextField) -> NSView {
        let grid = NSView()
        grid.translatesAutoresizingMaskIntoConstraints = false

        let balanceTitle = settlementFormulaTitle("本次提现")
        let netTitle = settlementFormulaTitle("当前提现利润")
        let settlementTitle = settlementFormulaTitle("结算结果")

        for view in [balanceTitle, netTitle, settlementTitle, balanceChange, netOutcome, settlementLine] {
            grid.addSubview(view)
        }

        let formulaLeading: CGFloat = 132
        NSLayoutConstraint.activate([
            balanceTitle.leadingAnchor.constraint(equalTo: grid.leadingAnchor),
            balanceTitle.topAnchor.constraint(equalTo: grid.topAnchor),
            balanceTitle.widthAnchor.constraint(equalToConstant: 110),
            balanceChange.leadingAnchor.constraint(equalTo: grid.leadingAnchor, constant: formulaLeading),
            balanceChange.trailingAnchor.constraint(equalTo: grid.trailingAnchor),
            balanceChange.firstBaselineAnchor.constraint(equalTo: balanceTitle.firstBaselineAnchor),

            netTitle.leadingAnchor.constraint(equalTo: grid.leadingAnchor),
            netTitle.topAnchor.constraint(equalTo: balanceTitle.bottomAnchor, constant: 14),
            netTitle.widthAnchor.constraint(equalToConstant: 110),
            netOutcome.leadingAnchor.constraint(equalTo: grid.leadingAnchor, constant: formulaLeading),
            netOutcome.trailingAnchor.constraint(equalTo: grid.trailingAnchor),
            netOutcome.firstBaselineAnchor.constraint(equalTo: netTitle.firstBaselineAnchor),

            settlementTitle.leadingAnchor.constraint(equalTo: grid.leadingAnchor),
            settlementTitle.topAnchor.constraint(equalTo: netTitle.bottomAnchor, constant: 14),
            settlementTitle.widthAnchor.constraint(equalToConstant: 110),
            settlementLine.leadingAnchor.constraint(equalTo: grid.leadingAnchor, constant: formulaLeading),
            settlementLine.trailingAnchor.constraint(equalTo: grid.trailingAnchor),
            settlementLine.firstBaselineAnchor.constraint(equalTo: settlementTitle.firstBaselineAnchor),
            settlementTitle.bottomAnchor.constraint(equalTo: grid.bottomAnchor)
        ])
        return grid
    }

    private func settlementFormulaTitle(_ title: String) -> NSTextField {
        let label = NSTextField(labelWithString: title)
        label.font = .systemFont(ofSize: 13, weight: .bold)
        label.textColor = .systemBlue
        label.alignment = .left
        label.translatesAutoresizingMaskIntoConstraints = false
        return label
    }

    private func settlementDivider() -> NSBox {
        let divider = NSBox()
        divider.boxType = .separator
        divider.translatesAutoresizingMaskIntoConstraints = false
        divider.heightAnchor.constraint(equalToConstant: 1).isActive = true
        return divider
    }

    private func configureSettlementField(_ field: NSTextField, placeholder: String, width: CGFloat) {
        field.placeholderString = placeholder
        field.alignment = .right
        field.font = .systemFont(ofSize: 13, weight: .semibold)
        field.translatesAutoresizingMaskIntoConstraints = false
        field.widthAnchor.constraint(equalToConstant: width).isActive = true
        field.heightAnchor.constraint(equalToConstant: 24).isActive = true
        field.target = self
        field.action = #selector(settlementInputsChanged)
        observeSettlementField(field)
    }

    private func observeSettlementField(_ field: NSTextField) {
        let observer = NotificationCenter.default.addObserver(
            forName: NSControl.textDidChangeNotification,
            object: field,
            queue: .main
        ) { [weak self] _ in
            self?.settlementInputsChanged()
        }
        settlementTextObservers.append(observer)
    }

    private func clearSettlementTextObservers() {
        for observer in settlementTextObservers {
            NotificationCenter.default.removeObserver(observer)
        }
        settlementTextObservers = []
    }

    private func settlementMetricValue() -> NSTextField {
        let value = NSTextField(labelWithString: "--")
        value.font = .systemFont(ofSize: 14, weight: .bold)
        value.alignment = .center
        value.lineBreakMode = .byClipping
        value.maximumNumberOfLines = 1
        value.cell?.wraps = false
        value.cell?.isScrollable = false
        value.translatesAutoresizingMaskIntoConstraints = false
        value.setContentHuggingPriority(.required, for: .horizontal)
        value.setContentCompressionResistancePriority(.required, for: .horizontal)
        return value
    }

    private func settlementMetric(title: String, value: NSTextField, color: NSColor) -> NSView {
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 2
        stack.alignment = .centerX
        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.font = .systemFont(ofSize: 11, weight: .bold)
        titleLabel.textColor = color
        titleLabel.alignment = .center
        titleLabel.lineBreakMode = .byTruncatingTail
        titleLabel.maximumNumberOfLines = 1
        stack.addArrangedSubview(titleLabel)
        stack.addArrangedSubview(value)
        NSLayoutConstraint.activate([
            titleLabel.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            titleLabel.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            value.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            value.trailingAnchor.constraint(equalTo: stack.trailingAnchor)
        ])
        return stack
    }

    private func settlementResultBlock(title: String, value: NSTextField, color: NSColor) -> NSView {
        let card = NSView()
        card.wantsLayer = true
        card.layer?.cornerRadius = 8
        card.layer?.backgroundColor = color.withAlphaComponent(0.08).cgColor
        card.layer?.borderColor = color.withAlphaComponent(0.22).cgColor
        card.layer?.borderWidth = 1
        card.translatesAutoresizingMaskIntoConstraints = false

        let content = NSStackView()
        content.orientation = .vertical
        content.spacing = 4
        content.alignment = .width
        content.translatesAutoresizingMaskIntoConstraints = false

        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.font = .systemFont(ofSize: 13, weight: .bold)
        titleLabel.textColor = color
        titleLabel.alignment = .center
        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        value.alignment = .center

        content.addArrangedSubview(titleLabel)
        content.addArrangedSubview(value)
        card.addSubview(content)
        NSLayoutConstraint.activate([
            card.widthAnchor.constraint(equalToConstant: 200),
            card.heightAnchor.constraint(equalToConstant: 76),
            content.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 12),
            content.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -12),
            content.centerYAnchor.constraint(equalTo: card.centerYAnchor),
            titleLabel.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            titleLabel.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            value.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            value.trailingAnchor.constraint(equalTo: content.trailingAnchor)
        ])
        return card
    }

    private struct SettlementAccount {
        let key: String
        let name: String
        let base: Double
        let current: Double
    }

    private struct SettlementCalculation {
        let withdrawalAmount: Double
        let baseDeduction: Double
        let settlementBase: Double
        let totalCost: Double
        let netOutcome: Double
        let partnerSettlement: Double
        let ownerSettlement: Double
        let partnerSharePercent: Double
    }

    private func settlementAccounts() -> [SettlementAccount] {
        guard let initial, let latest = history.last else { return [] }
        let count = max(initial.amounts.count, latest.amounts.count)
        let names = latest.accounts ?? initial.accounts ?? []
        return (0..<count).map { index in
            let name = index < names.count ? names[index] : "账号 \(index + 1)"
            return SettlementAccount(
                key: "\(index):\(name)",
                name: name,
                base: amount(at: index, in: initial),
                current: amount(at: index, in: latest)
            )
        }
    }

    private func settlementNumber(from field: NSTextField, fallback: Double) -> Double {
        let value = Double(field.stringValue.replacingOccurrences(of: ",", with: ""))
        return value ?? fallback
    }

    private func settlementCalculation() -> SettlementCalculation {
        let partnerSharePercent = 40.0
        let withdrawalAmount = effectiveWithdrawalAmount
        let baseDeduction = effectiveBaseDeduction
        let settlementBase = withdrawalAmount - baseDeduction
        let totalCost = cost
        let netOutcome = settlementBase - totalCost
        let partnerSettlement: Double
        if netOutcome >= 0 {
            partnerSettlement = partnerCost + netOutcome * partnerSharePercent / 100
        } else {
            partnerSettlement = partnerCost + netOutcome / 2
        }

        return SettlementCalculation(
            withdrawalAmount: withdrawalAmount,
            baseDeduction: baseDeduction,
            settlementBase: settlementBase,
            totalCost: totalCost,
            netOutcome: netOutcome,
            partnerSettlement: partnerSettlement,
            ownerSettlement: withdrawalAmount - partnerSettlement,
            partnerSharePercent: partnerSharePercent
        )
    }

    @objc private func settlementInputsChanged() {
        settlement.partnerName = "社会哥"
        settlement.partnerSharePercent = 40

        saveState()
        updateSettlementOutput()
    }

    private func updateSettlementOutput() {
        guard let labels = settlementLabels else { return }
        let calculation = settlementCalculation()

        animateMetricNumber(
            "settlement.socialReceivable",
            field: socialReceivableValue,
            value: calculation.partnerSettlement,
            formatter: { value in "\(value >= 0 ? "" : "-")\(String(format: "%.2f", abs(value))) 元" }
        )
        socialReceivableValue.textColor = calculation.partnerSettlement >= 0 ? .systemOrange : .systemRed
        animateMetricNumber(
            "settlement.xingReceivable",
            field: xingReceivableValue,
            value: calculation.ownerSettlement,
            formatter: { "\(String(format: "%.2f", $0)) 元" }
        )
        xingReceivableValue.textColor = calculation.ownerSettlement >= 0 ? .systemIndigo : .systemRed

        labels.balanceChange.stringValue = calculation.baseDeduction > 0
            ? "本次提现 \(money(calculation.withdrawalAmount)) - 基准余额 \(money(calculation.baseDeduction)) = \(money(calculation.settlementBase))"
            : "本次提现金额 = \(money(calculation.withdrawalAmount))"

        let netOutcomeLabel: String
        let settlementLine: String
        if calculation.netOutcome >= 0 {
            netOutcomeLabel = "分成基数 \(money(calculation.settlementBase)) - 总出资 \(money(calculation.totalCost)) = \(money(calculation.netOutcome))"
            settlementLine = "社会哥应收 = 出资 \(money(partnerCost)) + 当前提现利润 \(money(calculation.netOutcome)) × \(money(calculation.partnerSharePercent))% = \(money(calculation.partnerSettlement))；星星应收 = \(money(calculation.ownerSettlement))"
        } else {
            netOutcomeLabel = "分成基数 \(money(calculation.settlementBase)) - 总出资 \(money(calculation.totalCost)) = \(signedMoney(calculation.netOutcome))，双方各承担 \(money(abs(calculation.netOutcome) / 2))"
            settlementLine = calculation.partnerSettlement >= 0
                ? "社会哥应收 = 出资 \(money(partnerCost)) - 应承担亏损 \(money(abs(calculation.netOutcome) / 2)) = \(money(calculation.partnerSettlement))；星星应收 = \(money(calculation.ownerSettlement))"
                : "社会哥本次应补给星星 \(money(abs(calculation.partnerSettlement)))；星星无需向社会哥转款"
        }
        labels.netOutcome.stringValue = netOutcomeLabel
        labels.settlementLine.stringValue = settlementLine
    }

    private func buildHistoryPanel() -> NSView {
        let panel = NSView()
        panel.wantsLayer = true
        panel.layer?.backgroundColor = NSColor.white.cgColor

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 8
        stack.alignment = .width
        stack.translatesAutoresizingMaskIntoConstraints = false

        let title = NSTextField(labelWithString: "成本历史")
        title.font = .systemFont(ofSize: 14, weight: .bold)
        title.textColor = .secondaryLabelColor
        let clearButton = button("一键清空", action: #selector(confirmClearCostHistory))
        clearButton.controlSize = .regular
        clearButton.font = .systemFont(ofSize: 12, weight: .semibold)
        clearButton.toolTip = "清理成本历史表"

        let header = NSStackView()
        header.orientation = .horizontal
        header.spacing = 8
        header.alignment = .centerY
        let headerSpacer = NSView()
        headerSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        header.addArrangedSubview(title)
        header.addArrangedSubview(headerSpacer)
        header.addArrangedSubview(clearButton)

        let scroll = makeTableScroll(
            table: historyTable,
            columns: [
                ("序号", "index", 90),
                ("时间", "time", 170),
                ("余额合计", "total", 190),
                ("扣成本后", "gross", 190),
                ("总利润", "afterCost", 210)
            ]
        )
        scroll.setContentHuggingPriority(.defaultLow, for: .horizontal)
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 390).isActive = true

        stack.addArrangedSubview(header)
        stack.addArrangedSubview(scroll)
        NSLayoutConstraint.activate([
            header.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            header.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            scroll.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: stack.trailingAnchor)
        ])
        panel.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: panel.topAnchor, constant: 8),
            stack.bottomAnchor.constraint(equalTo: panel.bottomAnchor, constant: -16)
        ])
        return panel
    }

    private func buildMetricRow() -> NSStackView {
        let metricRow = NSStackView(views: [
            metricCard(title: "成本合计", value: costValue, color: .systemBlue),
            metricCard(title: "现在余额合计", value: currentValue, color: .systemTeal),
            metricCard(title: "总利润", value: resultValue, color: .systemOrange),
            metricCard(title: "回本状态", value: remainingValue, color: .systemRed),
            metricCard(title: "社会哥应收", value: socialReceivableValue, color: .systemOrange),
            metricCard(title: "星星应收", value: xingReceivableValue, color: .systemIndigo)
        ])
        metricRow.orientation = .horizontal
        metricRow.spacing = 12
        metricRow.distribution = .fillEqually
        metricRow.translatesAutoresizingMaskIntoConstraints = false
        metricRow.setContentHuggingPriority(.defaultLow, for: .horizontal)
        return metricRow
    }

    private func metricCard(title: String, value: NSTextField, color: NSColor) -> NSView {
        let card = NSView()
        card.wantsLayer = true
        card.layer?.cornerRadius = 8
        card.layer?.backgroundColor = color.withAlphaComponent(0.10).cgColor
        card.layer?.borderColor = color.withAlphaComponent(0.30).cgColor
        card.layer?.borderWidth = 1

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 8
        stack.alignment = .centerX
        stack.edgeInsets = NSEdgeInsets(top: 10, left: 8, bottom: 10, right: 8)
        stack.translatesAutoresizingMaskIntoConstraints = false

        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.font = .systemFont(ofSize: 12, weight: .bold)
        titleLabel.textColor = color
        titleLabel.alignment = .center
        titleLabel.lineBreakMode = .byTruncatingTail

        value.font = .boldSystemFont(ofSize: 17)
        value.textColor = .labelColor
        value.alignment = .center
        value.lineBreakMode = .byTruncatingTail
        value.maximumNumberOfLines = 1
        value.cell?.wraps = false
        value.cell?.isScrollable = false
        value.translatesAutoresizingMaskIntoConstraints = false
        value.heightAnchor.constraint(equalToConstant: 25).isActive = true
        value.setContentHuggingPriority(.defaultLow, for: .horizontal)
        value.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        stack.addArrangedSubview(titleLabel)
        stack.addArrangedSubview(value)
        card.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: card.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: card.trailingAnchor),
            stack.topAnchor.constraint(equalTo: card.topAnchor),
            stack.bottomAnchor.constraint(equalTo: card.bottomAnchor),
            value.leadingAnchor.constraint(equalTo: stack.leadingAnchor, constant: 4),
            value.trailingAnchor.constraint(equalTo: stack.trailingAnchor, constant: -4),
            card.heightAnchor.constraint(equalToConstant: 72)
        ])
        return card
    }

    private func makeTableScroll(table: NSTableView, columns: [(String, String, CGFloat)]) -> NSScrollView {
        table.delegate = self
        table.dataSource = self
        table.headerView = NSTableHeaderView()
        table.usesAlternatingRowBackgroundColors = true
        table.gridStyleMask = [.solidHorizontalGridLineMask, .solidVerticalGridLineMask]
        table.gridColor = NSColor.separatorColor.withAlphaComponent(0.65)
        table.rowHeight = 30
        table.columnAutoresizingStyle = .uniformColumnAutoresizingStyle
        table.selectionHighlightStyle = .none
        table.intercellSpacing = NSSize(width: 0, height: 0)

        for column in table.tableColumns {
            table.removeTableColumn(column)
        }
        for item in columns {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(item.1))
            column.title = item.0
            column.width = item.2
            column.minWidth = 44
            column.headerCell.alignment = .center
            table.addTableColumn(column)
        }

        let scroll = NSScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = table
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.borderType = .lineBorder
        return scroll
    }

    @objc private func inputsChanged() {
        withdrawalAmount = moneyInputValue(withdrawalField)
        baseDeductionAmount = moneyInputValue(baseDeductionField)
        saveState()
        updateSettlementOutput()
        refreshOutput()
        scheduleStoredStateSyncToServer()
    }

    @objc private func toggleBaseDeduction() {
        useBaseDeduction = useBaseDeductionButton.state == .on
        baseDeductionField.isHidden = !useBaseDeduction
        if useBaseDeduction {
            baseDeductionAmount = moneyInputValue(baseDeductionField)
        }
        saveState()
        refreshOutput()
        scheduleStoredStateSyncToServer()
    }

    @objc private func addCost() {
        let alert = NSAlert()
        alert.messageText = "累加成本"
        alert.informativeText = "填写日期、备注和金额，确认后会追加到星星出资。"
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")

        let datePicker = NSDatePicker()
        datePicker.datePickerStyle = .textFieldAndStepper
        datePicker.datePickerElements = [.yearMonthDay]
        datePicker.dateValue = Date()
        datePicker.translatesAutoresizingMaskIntoConstraints = false
        datePicker.widthAnchor.constraint(equalToConstant: 220).isActive = true

        let noteField = NSTextField(string: "")
        noteField.placeholderString = "备注"
        noteField.translatesAutoresizingMaskIntoConstraints = false
        noteField.widthAnchor.constraint(equalToConstant: 220).isActive = true

        let amountField = NSTextField(string: "")
        amountField.placeholderString = "金额"
        amountField.alignment = .right
        amountField.translatesAutoresizingMaskIntoConstraints = false
        amountField.widthAnchor.constraint(equalToConstant: 220).isActive = true

        let rows: [(String, NSView)] = [
            ("日期", datePicker),
            ("备注", noteField),
            ("金额", amountField)
        ]
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 8
        stack.alignment = .leading
        stack.translatesAutoresizingMaskIntoConstraints = false
        for row in rows {
            let line = NSStackView()
            line.orientation = .horizontal
            line.spacing = 10
            line.alignment = .centerY
            let title = label(row.0)
            title.widthAnchor.constraint(equalToConstant: 42).isActive = true
            line.addArrangedSubview(title)
            line.addArrangedSubview(row.1)
            stack.addArrangedSubview(line)
        }

        let container = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: 112))
        container.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 10),
            stack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -10),
            stack.topAnchor.constraint(equalTo: container.topAnchor, constant: 8),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: container.bottomAnchor, constant: -8)
        ])
        alert.accessoryView = container

        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let increment = moneyInputValue(amountField) ?? 0
        guard increment != 0 else {
            showFeedback("请输入要累加的成本金额。", color: .systemOrange)
            return
        }
        let note = noteField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let addition = CostAddition(
            id: UUID().uuidString,
            date: datePicker.dateValue,
            note: note,
            amount: increment,
            createdAt: Date()
        )
        costAdditions.append(addition)
        costAdditions.sort { $0.date < $1.date }
        let newCost = ownerCost + increment
        costField.stringValue = money(newCost)
        let noteText = note.isEmpty ? "" : "（\(note)）"
        showFeedback("已累加星星出资：\(signedMoney(increment)) 元\(noteText)，星星当前出资 \(money(newCost)) 元。", color: .systemGreen)
        saveState()
        refreshOutput()
        syncCostAdditionToServer(addition)
    }

    @objc private func showCostAdditionHistory() {
        guard !costAdditions.isEmpty else {
            showFeedback("暂无累加成本历史。", color: .systemOrange)
            return
        }
        let alert = NSAlert()
        alert.messageText = "累加成本历史"
        alert.alertStyle = .informational
        alert.addButton(withTitle: "关闭")
        alert.addButton(withTitle: "一键清空")

        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 640, height: 220))
        textView.string = costAdditionHistoryText()
        textView.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.isEditable = false
        textView.isSelectable = true
        textView.textContainerInset = NSSize(width: 8, height: 8)

        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 660, height: 240))
        scroll.documentView = textView
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .lineBorder
        alert.accessoryView = scroll

        if alert.runModal() == .alertSecondButtonReturn {
            confirmClearCostAdditions()
        }
    }

    private func costAdditionHistoryText() -> String {
        let rows = costAdditions.sorted { $0.date < $1.date }
        var lines = [
            "│ 序号 │ 日期                │ 金额         │ 备注 │",
            "├──────┼─────────────────────┼──────────────┼──────┤"
        ]
        for (index, item) in rows.enumerated() {
            let note = item.note.isEmpty ? "-" : item.note
            lines.append("│ \(index + 1) │ \(formatDate(item.date)) │ \(signedMoney(item.amount)) │ \(note) │")
        }
        lines.append("")
        lines.append("合计：\(signedMoney(rows.reduce(0) { $0 + $1.amount })) 元")
        return lines.joined(separator: "\n")
    }

    private func confirmClearCostAdditions() {
        guard !costAdditions.isEmpty else { return }
        let alert = NSAlert()
        alert.messageText = "清空累加成本？"
        alert.informativeText = "会删除全部累加成本历史，并从星星出资里扣回这些追加金额。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "确认清空")
        alert.addButton(withTitle: "取消")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        let total = costAdditions.reduce(0) { $0 + $1.amount }
        let newCost = max(ownerCost - total, 0)
        let removedCount = costAdditions.count
        costAdditions = []
        costField.stringValue = money(newCost)
        saveState()
        refreshOutput()
        showFeedback("已清空 \(removedCount) 条累加成本，星星当前出资 \(money(newCost)) 元。", color: .systemGreen)
        clearServerCostAdditions()
    }

    @objc private func importInitial() {
        pasteFromClipboard(asInitial: true)
    }

    @objc private func importLatest() {
        pasteFromClipboard(asInitial: false)
    }

    @objc private func resetAll() {
        initial = nil
        history = []
        withdrawalAmount = nil
        baseDeductionAmount = nil
        useBaseDeduction = false
        withdrawalField.stringValue = ""
        baseDeductionField.stringValue = ""
        baseDeductionField.isHidden = true
        useBaseDeductionButton.state = .off
        partnerCostField.stringValue = "0"
        costAdditions = []
        settlement = .default
        UserDefaults.standard.removeObject(forKey: "StoredState")
        showFeedback("成本计算数据已重置。", color: .systemGreen)
        refreshOutput()
    }

    @objc private func editBalanceAccounts() {
        let alert = NSAlert()
        alert.messageText = "余额账号配置"
        alert.informativeText = "每行一个账号：名称---api_key。接口地址统一使用 \(defaultBalanceBaseURL)，密钥保存在本机钥匙串。"
        alert.addButton(withTitle: "保存")
        alert.addButton(withTitle: "导入")
        alert.addButton(withTitle: "导出脱敏")
        alert.addButton(withTitle: "完整备份")
        alert.addButton(withTitle: "取消")

        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 720, height: 220))
        textView.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.string = formatBalanceAccountsForEditing(balanceAccounts)
        textView.isEditable = true
        textView.isSelectable = true
        textView.allowsUndo = true
        textView.isAutomaticQuoteSubstitutionEnabled = false
        textView.isAutomaticDashSubstitutionEnabled = false
        textView.isAutomaticTextReplacementEnabled = false

        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 740, height: 240))
        scroll.documentView = textView
        scroll.hasVerticalScroller = true
        scroll.borderType = .lineBorder
        alert.accessoryView = scroll

        let response = alert.runModal()
        if response == .alertSecondButtonReturn {
            importBalanceAccountsFromPasteboard(into: textView)
            return
        }
        if response == .alertThirdButtonReturn {
            exportBalanceAccounts(masked: true)
            return
        }
        if response.rawValue == NSApplication.ModalResponse.alertFirstButtonReturn.rawValue + 3 {
            confirmAndExportFullBalanceAccounts()
            return
        }
        guard response == .alertFirstButtonReturn else { return }
        let accounts = parseBalanceAccounts(textView.string)
        guard !accounts.isEmpty else {
            showError("至少配置一个余额账号。")
            return
        }
        balanceAccounts = accounts
        if saveBalanceAccounts(accounts) {
            showFeedback("余额账号已保存。", color: .systemGreen)
        } else {
            showFeedback("余额账号保存失败。", color: .systemRed)
        }
    }

    private func importBalanceAccountsFromPasteboard(into textView: NSTextView) {
        guard let text = NSPasteboard.general.string(forType: .string), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            showError("剪贴板里没有可导入的文本。")
            return
        }
        let accounts = parseBalanceAccounts(text)
        guard !accounts.isEmpty else {
            showError("没有解析到账号。支持：名称---api_key。")
            return
        }
        balanceAccounts = accounts
        if saveBalanceAccounts(accounts) {
            showFeedback("已从剪贴板导入 \(accounts.count) 个余额账号。", color: .systemGreen)
        }
        textView.string = formatBalanceAccountsForEditing(accounts)
    }

    private func exportBalanceAccounts(masked: Bool) {
        let payload = balanceAccounts.map {
            [
                "name": $0.name,
                "base_url": $0.baseURL,
                "api_key": masked ? maskedKey($0.apiKey) : $0.apiKey
            ]
        }
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted]),
              let text = String(data: data, encoding: .utf8) else {
            showError("导出失败。")
            return
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        showFeedback(masked ? "已复制脱敏账号配置。" : "已复制完整账号备份，请妥善保管。", color: masked ? .systemGreen : .systemOrange)
    }

    private func confirmAndExportFullBalanceAccounts() {
        let alert = NSAlert()
        alert.messageText = "导出完整备份？"
        alert.informativeText = "完整备份会包含 API Key。只建议用于你自己的离线备份，不要发到群里或提交到 GitHub。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "复制完整备份")
        alert.addButton(withTitle: "取消")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        exportBalanceAccounts(masked: false)
    }

    private func maskedKey(_ key: String) -> String {
        guard key.count > 10 else { return "******" }
        return "\(key.prefix(6))...\(key.suffix(4))"
    }

    @objc private func queryBalanceAsInitial() {
        queryBalances(asInitial: false, manual: true)
    }

    @objc private func queryBalanceAsLatest() {
        queryBalances(asInitial: false, manual: true)
    }

    private func queryBalances(asInitial: Bool, manual: Bool) {
        refreshServerData(manual: manual)
    }

    private func applyBalanceItems(_ items: [BalanceQueryItem], asInitial: Bool) {
        let amounts = items.map(\.balance)
        let accounts = items.map { $0.account.name }
        let snapshot = Snapshot(date: Date(), total: amounts.reduce(0, +), amounts: amounts, accounts: accounts)
        if initial == nil {
            initial = snapshot
        }
        history.append(snapshot)
        trimBalanceHistory()
        if withdrawalField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let roundedWithdrawal = roundedWithdrawalAmount(snapshot.total)
            withdrawalAmount = roundedWithdrawal
            withdrawalField.stringValue = money(roundedWithdrawal)
        }
        showFeedback("已查询最新余额：\(money(snapshot.total))。", color: .systemGreen)
        saveState()
        refreshOutput()
    }

    private func fetchAllBalances(completion: @escaping (Result<[BalanceQueryItem], Error>) -> Void) {
        let accounts = balanceAccounts
        let group = DispatchGroup()
        let lock = NSLock()
        var items: [BalanceQueryItem] = []
        var failures: [String] = []

        for account in accounts {
            group.enter()
            fetchBalance(account: account) { result in
                lock.lock()
                switch result {
                case .success(let item):
                    items.append(item)
                case .failure(let error):
                    failures.append("\(account.name)：\(error.localizedDescription)")
                }
                lock.unlock()
                group.leave()
            }
        }

        group.notify(queue: .main) {
            if !failures.isEmpty {
                completion(.failure(AppError(failures.joined(separator: "\n"))))
                return
            }
            let ordered = accounts.compactMap { account in
                items.first { $0.account.name == account.name && $0.account.baseURL == account.baseURL }
            }
            completion(.success(ordered))
        }
    }

    private func fetchBalance(account: BalanceAccount, completion: @escaping (Result<BalanceQueryItem, Error>) -> Void) {
        guard let url = balanceURL(baseURL: account.baseURL) else {
            completion(.failure(AppError("Base URL 无效")))
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 20
        request.setValue("Bearer \(account.apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("cc-switch/1.0", forHTTPHeaderField: "User-Agent")

        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error {
                completion(.failure(error))
                return
            }
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard (200..<300).contains(statusCode), let data else {
                completion(.failure(AppError("余额接口 HTTP \(statusCode)")))
                return
            }
            do {
                let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                guard let json else {
                    completion(.failure(AppError("余额接口返回不是 JSON")))
                    return
                }
                let source = (json["data"] as? [String: Any]) ?? json
                let balance = Self.extractBalance(from: source)
                guard let balance else {
                    completion(.failure(AppError("余额字段缺失：\(Self.responsePreview(data))")))
                    return
                }
                let unit = (source["unit"] as? String) ?? "USD"
                completion(.success(BalanceQueryItem(account: account, balance: balance, unit: unit)))
            } catch {
                completion(.failure(AppError("余额接口返回格式不正确：\(Self.responsePreview(data))")))
            }
        }.resume()
    }

    private func balanceURL(baseURL: String) -> URL? {
        let trimmed = baseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !trimmed.isEmpty else { return nil }
        return URL(string: "\(trimmed)/v1/usage")
    }

    private static func doubleValue(_ value: Any?) -> Double? {
        if let value = value as? Double { return value }
        if let value = value as? Int { return Double(value) }
        if let value = value as? String { return Double(value) }
        return nil
    }

    private static func extractBalance(from source: [String: Any]) -> Double? {
        if let value = doubleValue(source["balance"])
            ?? doubleValue(source["remaining"])
            ?? doubleValue(source["totalBalance"])
            ?? doubleValue(source["total_balance"])
            ?? doubleValue(source["available_balance"])
            ?? doubleValue(source["amount"]) {
            return value
        }
        if let infos = source["balance_infos"] as? [[String: Any]],
           let first = infos.first {
            return doubleValue(first["total_balance"])
                ?? doubleValue(first["balance"])
                ?? doubleValue(first["remaining"])
                ?? doubleValue(first["topped_up_balance"])
        }
        return nil
    }

    private static func responsePreview(_ data: Data) -> String {
        let text = String(data: data, encoding: .utf8) ?? "<非 UTF-8 数据 \(data.count) bytes>"
        let compact = text
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if compact.count <= 200 { return compact }
        return String(compact.prefix(200)) + "..."
    }

    private func restartBalancePollingTimer() {
        balancePollingTimer?.invalidate()
        balancePollingTimer = Timer.scheduledTimer(withTimeInterval: defaultPollingMinutes * 60, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.queryBalances(asInitial: false, manual: false)
        }
    }

    @objc private func confirmClearPoolHistory(_ sender: NSButton) {
        guard let groupName = sender.identifier?.rawValue else { return }
        let groupRows = poolHistory.filter { $0.groupName == groupName }
        guard !groupRows.isEmpty else {
            showFeedback("\(groupName) 暂无可清空的历史。", color: .systemOrange)
            return
        }

        let alert = NSAlert()
        alert.messageText = "清空\(groupName)历史？"
        alert.informativeText = "可以只保留最新一条数据，或清除这个号池的全部历史。其他号池历史不会受影响。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "清除到剩余最后一条")
        alert.addButton(withTitle: "全部清除")
        alert.addButton(withTitle: "取消")

        switch alert.runModal() {
        case .alertFirstButtonReturn:
            trimPoolHistoryToLatest(groupName: groupName)
        case .alertSecondButtonReturn:
            clearPoolHistory(groupName: groupName)
        default:
            break
        }
    }

    private func trimPoolHistoryToLatest(groupName: String) {
        guard let latest = latestPoolSnapshot(groupName: groupName) else { return }
        let beforeCount = poolHistory.filter { $0.groupName == groupName }.count
        poolHistory.removeAll { $0.groupName == groupName }
        poolHistory.append(latest)
        savePoolState()
        showFeedback("\(groupName) 已清除到只剩最新 1 条，移除 \(max(beforeCount - 1, 0)) 条。", color: .systemGreen)
        refreshOutput()
    }

    private func clearPoolHistory(groupName: String) {
        let beforeCount = poolHistory.filter { $0.groupName == groupName }.count
        poolHistory.removeAll { $0.groupName == groupName }
        savePoolState()
        showFeedback("\(groupName) 已全部清除，移除 \(beforeCount) 条。", color: .systemGreen)
        refreshOutput()
    }

    @objc private func confirmClearCostHistory() {
        guard !history.isEmpty else {
            showFeedback("成本历史暂无可清空的数据。", color: .systemOrange)
            return
        }

        let alert = NSAlert()
        alert.messageText = "清空成本历史？"
        alert.informativeText = "可以只保留最新一条成本历史，或清除下面表格的全部历史。出资、提现金额和账号配置不会被清空。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "清除到剩余最后一条")
        alert.addButton(withTitle: "全部清除")
        alert.addButton(withTitle: "取消")

        switch alert.runModal() {
        case .alertFirstButtonReturn:
            trimCostHistoryToLatest()
        case .alertSecondButtonReturn:
            clearCostHistory()
        default:
            break
        }
    }

    private func trimCostHistoryToLatest() {
        guard let latest = history.last else { return }
        let beforeCount = history.count
        history = [latest]
        saveState()
        showFeedback("成本历史已清除到只剩最新 1 条，移除 \(max(beforeCount - 1, 0)) 条。", color: .systemGreen)
        refreshOutput()
    }

    private func clearCostHistory() {
        let beforeCount = history.count
        history = []
        saveState()
        showFeedback("成本历史已全部清除，移除 \(beforeCount) 条。", color: .systemGreen)
        refreshOutput()
    }

    @objc private func editWarningSettings() {
        let existing = loadSMTPSettings() ?? smtpSettings
        let alert = NSAlert()
        alert.messageText = "掉号预警设置"
        alert.informativeText = "QQ 邮箱 SMTP：进入 QQ 邮箱网页版 -> 设置 -> 账号 -> POP3/IMAP/SMTP 服务，开启后生成授权码。服务器 smtp.qq.com，端口 465，密码填授权码。"
        alert.addButton(withTitle: "保存")
        alert.addButton(withTitle: "取消")

        let recipientField = NSTextField(string: existing.recipient)
        recipientField.placeholderString = "预警收件邮箱"
        let hostField = NSTextField(string: existing.host)
        hostField.placeholderString = "SMTP 服务器"
        let portField = NSTextField(string: "\(existing.port)")
        portField.placeholderString = "端口"
        let usernameField = NSTextField(string: existing.username)
        usernameField.placeholderString = "发件邮箱账号"
        let passwordField = NSSecureTextField(string: existing.password)
        passwordField.placeholderString = "SMTP 授权码"
        for field in [recipientField, hostField, portField, usernameField, passwordField] {
            field.translatesAutoresizingMaskIntoConstraints = false
            field.widthAnchor.constraint(equalToConstant: 360).isActive = true
            field.heightAnchor.constraint(equalToConstant: 26).isActive = true
        }
        let stack = NSStackView(views: [recipientField, hostField, portField, usernameField, passwordField])
        stack.orientation = .vertical
        stack.spacing = 7
        stack.translatesAutoresizingMaskIntoConstraints = false
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 380, height: 160))
        container.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 10),
            stack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -10),
            stack.topAnchor.constraint(equalTo: container.topAnchor, constant: 8)
        ])
        alert.accessoryView = container
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        smtpSettings = SMTPSettings(
            host: hostField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
            port: Int(portField.stringValue) ?? 465,
            username: usernameField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
            password: passwordField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
            recipient: normalizedEmail(recipientField.stringValue)
        )
        _ = saveSMTPSettings(smtpSettings)
        savePoolState()
        showFeedback("掉号预警设置已保存。", color: .systemGreen)
    }

    @objc private func selectPoolGroups() {
        fetchServerState(manual: false)
        presentPoolGroupPicker()
    }

    @objc private func editPoolCredentials() {
        _ = promptForPoolCredentials(force: true)
    }

    @objc private func poolPollingChanged() {
        let value = Double(poolPollingField.stringValue.replacingOccurrences(of: ",", with: "")) ?? defaultPollingMinutes
        poolPollingMinutes = normalizedPollingMinutes(value)
        poolPollingField.stringValue = money(poolPollingMinutes)
        savePoolState()
        restartPoolPollingTimer()
    }

    @objc private func refreshPoolsFromAPIButton() {
        refreshPoolsFromAPI(isAutomatic: false)
    }

    private func refreshPoolsFromAPI(isAutomatic: Bool) {
        refreshServerData(manual: !isAutomatic)
    }

    private func restartPoolPollingTimer() {
        poolPollingTimer?.invalidate()
        let interval = normalizedPollingMinutes(poolPollingMinutes) * 60
        poolPollingTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.refreshPoolsFromAPI(isAutomatic: true)
        }
    }

    private func normalizedPollingMinutes(_ value: Double) -> Double {
        let clamped = min(max(value, defaultPollingMinutes), 1440)
        return max(defaultPollingMinutes, (clamped / defaultPollingMinutes).rounded() * defaultPollingMinutes)
    }

    private func presentPoolGroupPicker() {
        let alert = NSAlert()
        alert.messageText = "选择平台共享容量池"
        alert.informativeText = "刷新时只会同步勾选的分组。"
        alert.addButton(withTitle: "保存")
        alert.addButton(withTitle: "取消")

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 6
        stack.alignment = .leading
        stack.translatesAutoresizingMaskIntoConstraints = false

        let orderedGroups = availablePoolGroups.sorted { lhs, rhs in
            let priority = ["PLUS共享号池": 0, "K12共享号池": 1]
            return (priority[lhs] ?? 99, lhs) < (priority[rhs] ?? 99, rhs)
        }
        var checks: [(String, NSButton)] = []
        for group in orderedGroups {
            let check = NSButton(checkboxWithTitle: group, target: nil, action: nil)
            check.state = selectedPoolGroups.contains(group) ? .on : .off
            check.font = .systemFont(ofSize: 13, weight: .medium)
            checks.append((group, check))
            stack.addArrangedSubview(check)
        }

        let containerHeight = min(CGFloat(max(orderedGroups.count, 1)) * 28 + 16, 300)
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 360, height: containerHeight))
        container.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 10),
            stack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -10),
            stack.topAnchor.constraint(equalTo: container.topAnchor, constant: 8),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: container.bottomAnchor, constant: -8)
        ])
        alert.accessoryView = container

        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let selected = checks.filter { $0.1.state == .on }.map(\.0)
        guard !selected.isEmpty else {
            showError("至少选择一个号池分组。")
            return
        }
        selectedPoolGroups = selected
        hasLocalPoolSelection = true
        savePoolState()
        updatePoolGroupsLabel()
        if tabControl.selectedSegment == 1 {
            switchTab(nil)
            refreshOutput()
        } else {
            refreshOutput()
        }
        showFeedback("已选择：\(selected.joined(separator: "、"))", color: .systemGreen)
    }

    private func updatePoolGroupsLabel() {
        poolGroupsLabel.stringValue = selectedPoolGroups.joined(separator: "、")
        poolGroupsLabel.toolTip = poolGroupsLabel.stringValue
    }

    private func applyDashboardSummaries(_ summaries: [APIGroupSummary], selectedGroups: [String]) {
        let now = Date()
        let selected = Set(selectedGroups)
        let snapshots = summaries
            .filter { selected.contains($0.groupName) }
            .map { Self.poolSnapshot(from: $0, date: now) }

        guard !snapshots.isEmpty else {
            let available = summaries.map(\.groupName).joined(separator: "、")
            showFeedback("没找到选中分组。接口返回：\(available)", color: .systemOrange, autoHide: false)
            return
        }

        poolHistory.append(contentsOf: snapshots)
        trimPoolHistory()
        checkPoolDropWarnings(for: snapshots)
        savePoolState()
        refreshOutput()
        let names = snapshots.map(\.groupName).joined(separator: "、")
        showFeedback("已同步 \(names)，新增 \(snapshots.count) 条历史。", color: .systemGreen)
    }

    private func checkPoolDropWarnings(for snapshots: [PoolSnapshot]) {
        for snapshot in snapshots {
            let windowStart = snapshot.date.addingTimeInterval(-10 * 60)
            guard let baseline = poolHistory
                .filter({ $0.groupName == snapshot.groupName && $0.date >= windowStart && $0.date < snapshot.date })
                .min(by: { $0.date < $1.date }) else {
                continue
            }
            let drop = baseline.total - snapshot.total
            guard drop > 100 else { continue }
            let minuteKey = Int(snapshot.date.timeIntervalSince1970 / 60)
            let dedupKey = "\(snapshot.groupName):\(minuteKey)"
            guard !poolWarningDedup.contains(dedupKey) else { continue }
            poolWarningDedup.insert(dedupKey)
            let message = "\(snapshot.groupName) 10 分钟内减少 \(drop) 个账号（\(baseline.total) -> \(snapshot.total)）。"
            showFeedback("掉号预警：\(message)", color: .systemRed, autoHide: false)
            sendPoolWarningEmailIfConfigured(subject: "GPT分析器掉号预警", body: message)
        }
    }

    private func sendPoolWarningEmailIfConfigured(subject: String, body: String) {
        guard !smtpSettings.recipient.isEmpty else { return }
        guard !smtpSettings.host.isEmpty, !smtpSettings.username.isEmpty, !smtpSettings.password.isEmpty else {
            showFeedback("掉号预警已触发；SMTP 未配置完整，未发送邮件。", color: .systemOrange, autoHide: false)
            return
        }
        showFeedback("掉号预警已触发；邮件发送通道已配置，待接入 SMTP 发送。", color: .systemOrange, autoHide: false)
    }

    private func fetchQuotaDashboard(retryAfterLogin: Bool, completion: @escaping (Result<[APIGroupSummary], Error>) -> Void) {
        guard let url = URL(string: "https://cf.ai-pixel.online/api/v1/accounts/quota-dashboard?timezone=Asia%2FShanghai") else {
            completion(.failure(AppError("账号池接口地址无效。")))
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        if let poolAccessToken, !poolAccessToken.isEmpty {
            request.setValue("Bearer \(poolAccessToken)", forHTTPHeaderField: "Authorization")
        }
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            if let error {
                completion(.failure(error))
                return
            }
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
            if statusCode == 401 || self?.poolAccessToken == nil {
                guard retryAfterLogin else {
                    completion(.failure(AppError("登录已失效，请重新设置接口账号。")))
                    return
                }
                self?.loginForPoolAPI { loginResult in
                    switch loginResult {
                    case .success:
                        self?.fetchQuotaDashboard(retryAfterLogin: false, completion: completion)
                    case .failure(let error):
                        completion(.failure(error))
                    }
                }
                return
            }
            guard (200..<300).contains(statusCode), let data else {
                completion(.failure(AppError("账号池接口返回异常：HTTP \(statusCode)。")))
                return
            }
            do {
                let decoded = try JSONDecoder().decode(APIQuotaDashboardResponse.self, from: data)
                completion(.success(decoded.data.platform.groupSummaries))
            } catch {
                let message = Self.apiErrorMessage(from: data) ?? error.localizedDescription
                completion(.failure(AppError("账号池接口解析失败：\(message)")))
            }
        }.resume()
    }

    private func loginForPoolAPI(completion: @escaping (Result<Void, Error>) -> Void) {
        if let credentials = loadPoolCredentials() {
            performPoolLogin(credentials: credentials, completion: completion)
            return
        }
        DispatchQueue.main.async { [weak self] in
            guard let self, let credentials = self.promptForPoolCredentials(force: false) else {
                completion(.failure(AppError("请先设置接口账号。")))
                return
            }
            self.performPoolLogin(credentials: credentials, completion: completion)
        }
    }

    private func performPoolLogin(credentials: PoolCredentials, completion: @escaping (Result<Void, Error>) -> Void) {
        guard let url = URL(string: "https://cf.ai-pixel.online/api/v1/auth/login") else {
            completion(.failure(AppError("登录接口地址无效。")))
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let body: [String: String] = [
            "email": credentials.email,
            "password": credentials.password,
            "login_agreement_revision": "a90464c54fba46d4"
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)

        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            if let error {
                completion(.failure(error))
                return
            }
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard (200..<300).contains(statusCode), let data else {
                let message = Self.apiErrorMessage(from: data) ?? "HTTP \(statusCode)"
                completion(.failure(AppError("登录失败：\(message)。请点“接口账号”重新保存一次账号密码。")))
                return
            }
            do {
                let decoded = try JSONDecoder().decode(APILoginResponse.self, from: data)
                DispatchQueue.main.async {
                    self?.poolAccessToken = decoded.data.accessToken
                    self?.poolRefreshToken = decoded.data.refreshToken
                    self?.savePoolState()
                    completion(.success(()))
                }
            } catch {
                let message = Self.apiErrorMessage(from: data) ?? error.localizedDescription
                completion(.failure(AppError("登录结果解析失败：\(message)")))
            }
        }.resume()
    }

    private func promptForPoolCredentials(force: Bool) -> PoolCredentials? {
        let existing = loadPoolCredentials()
        let alert = NSAlert()
        alert.messageText = force ? "设置接口账号" : "首次使用接口同步"
        alert.informativeText = ""
        alert.addButton(withTitle: "保存")
        alert.addButton(withTitle: "取消")

        let emailField = NSTextField(string: existing?.email ?? "")
        emailField.placeholderString = "邮箱"
        let passwordField = NSSecureTextField(string: existing?.password ?? "")
        passwordField.placeholderString = "密码"
        for field in [emailField, passwordField] {
            field.widthAnchor.constraint(equalToConstant: 320).isActive = true
            field.heightAnchor.constraint(equalToConstant: 28).isActive = true
        }
        let tip = NSTextField(wrappingLabelWithString: "账号密码只保存在本机 macOS 钥匙串，用于 token 失效后自动重新登录。")
        tip.font = .systemFont(ofSize: 12, weight: .medium)
        tip.textColor = .secondaryLabelColor
        tip.maximumNumberOfLines = 2
        tip.translatesAutoresizingMaskIntoConstraints = false
        tip.widthAnchor.constraint(equalToConstant: 320).isActive = true

        let stack = NSStackView(views: [tip, emailField, passwordField])
        stack.orientation = .vertical
        stack.spacing = 8
        stack.alignment = .width
        stack.translatesAutoresizingMaskIntoConstraints = false

        let container = NSView(frame: NSRect(x: 0, y: 0, width: 340, height: 112))
        container.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 10),
            stack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -10),
            stack.topAnchor.constraint(equalTo: container.topAnchor, constant: 4),
            stack.bottomAnchor.constraint(equalTo: container.bottomAnchor, constant: -4)
        ])
        alert.accessoryView = container

        guard alert.runModal() == .alertFirstButtonReturn else { return nil }
        var email = emailField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if !email.contains("@"), !email.isEmpty {
            email += "@qq.com"
        }
        let password = passwordField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !email.isEmpty, !password.isEmpty else {
            showError("接口账号和密码不能为空。")
            return nil
        }
        let credentials = PoolCredentials(email: email, password: password)
        if savePoolCredentials(credentials) {
            poolAccessToken = nil
            poolRefreshToken = nil
            savePoolState()
            showFeedback("接口账号已保存到本机钥匙串。", color: .systemGreen)
        } else {
            showFeedback("钥匙串保存失败，请稍后重试。", color: .systemRed)
        }
        return credentials
    }

    private static func apiErrorMessage(from data: Data?) -> String? {
        guard let data, !data.isEmpty else { return nil }
        if let decoded = try? JSONDecoder().decode(APIErrorResponse.self, from: data),
           let message = decoded.message,
           !message.isEmpty {
            return message
        }
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for key in ["message", "detail", "error"] {
                if let message = object[key] as? String, !message.isEmpty {
                    return message
                }
            }
        }
        return nil
    }

    private func pasteSmart() {
        guard let image = clipboardImage() else {
            showError("剪贴板里没有图片。请先复制截图，再回到应用按 Command+V。")
            return
        }
        guard beginPasteIfAllowed() else { return }
        recognize(image: image) { [weak self] result in
            switch result {
            case .success(let ocr):
                if let poolSnapshot = Self.extractPoolMetrics(from: ocr, date: Date()) {
                    self?.appendPoolSnapshot(poolSnapshot)
                    return
                }
                self?.applyCostOCR(ocr, asInitial: self?.initial == nil)
            case .failure(let error):
                self?.finishPaste(success: false)
                self?.showError(error.localizedDescription)
            }
        }
    }

    private func pasteFromClipboard(asInitial: Bool) {
        guard let image = clipboardImage() else {
            showError("剪贴板里没有图片。请先复制截图，再回到应用按 Command+V。")
            return
        }
        guard beginPasteIfAllowed() else { return }
        recognize(image: image) { [weak self] result in
            switch result {
            case .success(let ocr):
                self?.applyCostOCR(ocr, asInitial: asInitial)
            case .failure(let error):
                self?.finishPaste(success: false)
                self?.showError(error.localizedDescription)
            }
        }
    }

    private func applyCostOCR(_ ocr: OCRResult, asInitial: Bool?) {
        guard !ocr.amounts.isEmpty else {
            finishPaste(success: false)
            showError("没有识别到余额金额，也没有识别到 PLUS/K12 账号池标题。请确认截图内容。")
            return
        }
        let snapshot = Snapshot(date: Date(), total: ocr.amounts.reduce(0, +), amounts: ocr.amounts, accounts: ocr.accounts)
        if asInitial ?? false {
            initial = snapshot
            history = []
        } else {
            if initial == nil {
                finishPaste(success: false)
                showError("请先粘贴基准截图。")
                return
            }
            history.append(snapshot)
            trimBalanceHistory()
            showFeedback("已识别最新截图：\(ocr.accounts.count) 个账号，\(ocr.amounts.count) 个余额。", color: .systemGreen)
        }
        if asInitial ?? false {
            showFeedback("已识别基准截图：\(ocr.accounts.count) 个账号，\(ocr.amounts.count) 个余额。", color: .systemGreen)
        }
        finishPaste(success: true)
        saveState()
        if tabControl.selectedSegment == 2 {
            switchTab(nil)
        } else {
            refreshOutput()
        }
    }

    private func clipboardImage() -> NSImage? {
        let pasteboard = NSPasteboard.general
        if let image = NSImage(pasteboard: pasteboard) {
            return image
        }
        for typeName in ["public.png", "public.tiff", "public.jpeg"] {
            if let data = pasteboard.data(forType: NSPasteboard.PasteboardType(typeName)),
               let image = NSImage(data: data) {
                return image
            }
        }
        return nil
    }

    private func recognize(image: NSImage, completion: @escaping (Result<OCRResult, Error>) -> Void) {
        guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            completion(.failure(AppError("图片无法读取。")))
            return
        }

        let request = VNRecognizeTextRequest { request, error in
            if let error {
                DispatchQueue.main.async { completion(.failure(error)) }
                return
            }
            let lines = ((request.results as? [VNRecognizedTextObservation]) ?? [])
                .compactMap { observation -> OCRLine? in
                    guard let text = observation.topCandidates(1).first?.string else { return nil }
                    return OCRLine(text: text, boundingBox: observation.boundingBox)
                }
                .sorted { left, right in
                    let yDelta = abs(left.boundingBox.midY - right.boundingBox.midY)
                    if yDelta > 0.012 {
                        return left.boundingBox.midY > right.boundingBox.midY
                    }
                    return left.boundingBox.midX < right.boundingBox.midX
                }
            let text = lines.map(\.text).joined(separator: "\n")
            let amounts = Self.extractAmounts(from: text)
            let accounts = Self.extractAccounts(from: text, expectedCount: amounts.count)
            DispatchQueue.main.async {
                completion(.success(OCRResult(text: text, amounts: amounts, accounts: accounts, lines: lines)))
            }
        }
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["zh-Hans", "en-US"]

        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }

    private static func extractAmounts(from text: String) -> [Double] {
        let pattern = #"(?<![A-Za-z0-9])([0-9]{1,5}\.[0-9]{2})(?![A-Za-z0-9])"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let nsText = text as NSString
        return regex.matches(in: text, range: NSRange(location: 0, length: nsText.length)).compactMap { match in
            guard match.numberOfRanges > 1 else { return nil }
            let value = nsText.substring(with: match.range(at: 1))
            return Double(value)
        }
    }

    private static func extractAccounts(from text: String, expectedCount: Int) -> [String] {
        let pattern = #"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"#
        let nsText = text as NSString
        var values: [String] = []
        if let regex = try? NSRegularExpression(pattern: pattern) {
            for match in regex.matches(in: text, range: NSRange(location: 0, length: nsText.length)) {
                let account = nsText.substring(with: match.range)
                if !values.contains(account) {
                    values.append(account)
                }
            }
        }
        if values.count < expectedCount {
            for index in values.count..<expectedCount {
                values.append("账号 \(index + 1)")
            }
        }
        if values.count > expectedCount {
            return Array(values.prefix(expectedCount))
        }
        return values
    }

    private static func extractPoolMetrics(from ocr: OCRResult, date: Date) -> PoolSnapshot? {
        extractPoolMetrics(from: ocr.text, positionedLines: ocr.lines, date: date)
    }

    private static func poolSnapshot(from summary: APIGroupSummary, date: Date) -> PoolSnapshot {
        let remaining5h = remainingCapacity(window: "5h", summary: summary)
        let remaining7d = remainingCapacity(window: "7d", summary: summary)
        let utilization5h = utilization(window: "5h", summary: summary)
        let utilization7d = utilization(window: "7d", summary: summary)
        let concurrentAvailable = max(summary.schedulableAccountCount - summary.rateLimitedAccountCount - summary.codexQuotaProtectedAccountCount - summary.errorAccountCount - summary.disabledAccountCount, 0)
        return PoolSnapshot(
            date: date,
            groupName: summary.groupName,
            status: statusText(from: summary.groupStatus),
            total: summary.accountCount,
            active: summary.activeAccountCount,
            schedulable: summary.schedulableAccountCount,
            remaining5h: remaining5h,
            remaining7d: remaining7d,
            utilization5h: utilization5h,
            utilization7d: utilization7d,
            concurrentAvailable: concurrentAvailable,
            concurrentTotal: summary.schedulableAccountCount,
            limited: summary.rateLimitedAccountCount,
            quotaProtected: summary.codexQuotaProtectedAccountCount,
            error: summary.errorAccountCount,
            disabled: summary.disabledAccountCount
        )
    }

    private static func remainingCapacity(window: String, summary: APIGroupSummary) -> Int? {
        guard let item = summary.usageWindows.first(where: { $0.window.caseInsensitiveCompare(window) == .orderedSame }),
              let percent = item.remainingCapacityPercent else {
            return nil
        }
        return Int((percent / 100).rounded())
    }

    private static func utilization(window: String, summary: APIGroupSummary) -> Double? {
        summary.usageWindows.first { $0.window.caseInsensitiveCompare(window) == .orderedSame }?.averageUtilization
    }

    private static func statusText(from status: String) -> String {
        switch status.lowercased() {
        case "active", "normal":
            return "正常"
        case "warning":
            return "警告"
        case "disabled":
            return "禁用"
        default:
            return status
        }
    }

    private static func extractPoolMetrics(from text: String, positionedLines: [OCRLine], date: Date) -> PoolSnapshot? {
        let compactText = text.replacingOccurrences(of: " ", with: "")
        let groupName: String
        if compactText.localizedCaseInsensitiveContains("PLUS") && compactText.contains("共享号池") {
            groupName = "PLUS共享号池"
        } else if compactText.localizedCaseInsensitiveContains("K12") && compactText.contains("共享号池") {
            groupName = "K12共享号池"
        } else {
            return nil
        }

        guard let total = firstInt(in: compactText, pattern: #"共([0-9,]+)"#) else { return nil }
        let active = firstInt(in: compactText, pattern: #"活跃([0-9,]+)"#) ?? 0
        let schedulable = firstInt(in: compactText, pattern: #"可调度([0-9,]+)"#) ?? 0
        let concurrent = firstPair(in: compactText, pattern: #"并发可用([0-9,]+)/([0-9,]+)"#) ?? (0, 0)
        let lines = text
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        let status = compactText.contains("资源紧张") ? "资源紧张" : (compactText.contains("正常") ? "正常" : "--")
        let positionedStatusCounts = statusCountsByPosition(lines: positionedLines)
        let textStatusCounts = statusClusterCounts(lines: lines)
        let statusCounts = positionedStatusCounts ?? textStatusCounts
        let disabled = statusValueByPosition(label: "禁用", lines: positionedLines)
            ?? countAfter(label: "禁用", lines: lines)
            ?? firstInt(in: compactText, pattern: #"禁用([0-9,]+)"#)
            ?? 0
        let remaining5h = remainingValueByPosition(windowLabel: "5h", lines: positionedLines)
            ?? remainingValue(windowLabel: "5h", lines: lines)
        let remaining7d = remainingValueByPosition(windowLabel: "7d", lines: positionedLines)
            ?? remainingValue(windowLabel: "7d", lines: lines)

        return PoolSnapshot(
            date: date,
            groupName: groupName,
            status: status,
            total: total,
            active: active,
            schedulable: schedulable,
            remaining5h: remaining5h,
            remaining7d: remaining7d,
            utilization5h: nil,
            utilization7d: nil,
            concurrentAvailable: concurrent.0,
            concurrentTotal: concurrent.1,
            limited: statusCounts.limited,
            quotaProtected: statusCounts.quotaProtected,
            error: statusCounts.error,
            disabled: disabled
        )
    }

    private static func firstInt(in text: String, pattern: String) -> Int? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)),
              match.numberOfRanges > 1 else { return nil }
        let value = (text as NSString).substring(with: match.range(at: 1)).replacingOccurrences(of: ",", with: "")
        return Int(value)
    }

    private static func firstPair(in text: String, pattern: String) -> (Int, Int)? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)),
              match.numberOfRanges > 2 else { return nil }
        let nsText = text as NSString
        let first = nsText.substring(with: match.range(at: 1)).replacingOccurrences(of: ",", with: "")
        let second = nsText.substring(with: match.range(at: 2)).replacingOccurrences(of: ",", with: "")
        guard let left = Int(first), let right = Int(second) else { return nil }
        return (left, right)
    }

    private static func statusClusterCounts(lines: [String]) -> (limited: Int, quotaProtected: Int, error: Int) {
        let inlineLimited = valueOnSameLine(label: "限流", lines: lines)
        let inlineQuota = valueOnSameLine(label: "额度保护", lines: lines)
        let inlineError = valueOnSameLine(label: "错误", lines: lines)
        if let inlineLimited, let inlineQuota, let inlineError {
            return (inlineLimited, inlineQuota, inlineError)
        }

        guard let start = lines.firstIndex(where: { $0.contains("限流") }) else {
            return (inlineLimited ?? 0, inlineQuota ?? 0, inlineError ?? 0)
        }
        let following = lines[(start + 1)..<lines.count]
        let values = following.compactMap { integerOnly($0) }
        if values.count >= 3 {
            return (values[0], values[1], values[2])
        }
        return (
            countAfter(label: "限流", lines: lines) ?? 0,
            countAfter(label: "额度保护", lines: lines) ?? 0,
            countAfter(label: "错误", lines: lines) ?? 0
        )
    }

    private static func valueOnSameLine(label: String, lines: [String]) -> Int? {
        for line in lines where line.contains(label) {
            let compact = line.replacingOccurrences(of: " ", with: "")
            if let value = firstInt(in: compact, pattern: "\(NSRegularExpression.escapedPattern(for: label))([0-9,]+)") {
                return value
            }
        }
        return nil
    }

    private static func countAfter(label: String, lines: [String]) -> Int? {
        guard let index = lines.firstIndex(where: { $0.contains(label) }) else { return nil }
        for line in lines.dropFirst(index + 1) {
            if let value = integerOnly(line) {
                return value
            }
            if line.contains("窗口") || line.localizedCaseInsensitiveContains("OpenAI") {
                return nil
            }
        }
        return nil
    }

    private static func statusCountsByPosition(lines: [OCRLine]) -> (limited: Int, quotaProtected: Int, error: Int)? {
        let limited = statusValueByPosition(label: "限流", lines: lines)
        let quotaProtected = statusValueByPosition(label: "限额保护", lines: lines)
            ?? statusValueByPosition(label: "额度保护", lines: lines)
        let error = statusValueByPosition(label: "错误", lines: lines)
        guard limited != nil || quotaProtected != nil || error != nil else { return nil }
        return (limited ?? 0, quotaProtected ?? 0, error ?? 0)
    }

    private static func statusValueByPosition(label: String, lines: [OCRLine]) -> Int? {
        guard let labelLine = positionedLine(containing: label, in: lines) else { return nil }
        if let inlineValue = firstIntAfterLabel(label: label, in: labelLine.text) {
            return inlineValue
        }

        return lines
            .filter { candidate in
                candidate.boundingBox.midY < labelLine.boundingBox.midY &&
                labelLine.boundingBox.midY - candidate.boundingBox.midY < 0.12 &&
                abs(candidate.boundingBox.midX - labelLine.boundingBox.midX) < 0.12
            }
            .sorted { left, right in
                let leftDistance = abs(left.boundingBox.midX - labelLine.boundingBox.midX) + abs(left.boundingBox.midY - labelLine.boundingBox.midY)
                let rightDistance = abs(right.boundingBox.midX - labelLine.boundingBox.midX) + abs(right.boundingBox.midY - labelLine.boundingBox.midY)
                return leftDistance < rightDistance
            }
            .compactMap { integerOnly($0.text) ?? firstInteger(in: $0.text) }
            .first
    }

    private static func firstIntAfterLabel(label: String, in text: String) -> Int? {
        let compact = text.replacingOccurrences(of: " ", with: "")
        let pattern = "\(NSRegularExpression.escapedPattern(for: label))([0-9,]+)"
        return firstInt(in: compact, pattern: pattern)
    }

    private static func firstInteger(in text: String) -> Int? {
        let compact = text.replacingOccurrences(of: ",", with: "")
        let pattern = #"([0-9]{1,6})"#
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: compact, range: NSRange(location: 0, length: (compact as NSString).length)),
              match.numberOfRanges > 1 else { return nil }
        return Int((compact as NSString).substring(with: match.range(at: 1)))
    }

    private static func firstDecimalInt(in text: String) -> Int? {
        let pattern = #"([0-9]{1,6}(?:\.[0-9]+)?)"#
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)),
              match.numberOfRanges > 1 else { return nil }
        let value = (text as NSString).substring(with: match.range(at: 1))
        guard let number = Double(value) else { return nil }
        return Int(number.rounded())
    }

    private static func remainingValueByPosition(windowLabel: String, lines: [OCRLine]) -> Int? {
        guard let windowLine = windowLine(label: windowLabel, lines: lines) else { return nil }
        let windowLines = linesInWindowColumn(windowLine: windowLine, lines: lines)
        let value = remainingValueNearLabel(lines: windowLines) ?? remainingValueInWindowColumn(lines: windowLines)
        return plausibleRemaining(value)
    }

    private static func windowLine(label: String, lines: [OCRLine]) -> OCRLine? {
        let lowerLabel = label.lowercased()
        return lines.first { line in
            let compact = line.text.replacingOccurrences(of: " ", with: "").lowercased()
            return compact.contains(lowerLabel) && compact.contains("窗口")
        }
    }

    private static func linesInWindowColumn(windowLine: OCRLine, lines: [OCRLine]) -> [OCRLine] {
        let windowLines = lines.filter { line in
            let compact = line.text.replacingOccurrences(of: " ", with: "").lowercased()
            return compact.contains("窗口") && (compact.contains("5h") || compact.contains("7d"))
        }
        let sortedWindows = windowLines.sorted { $0.boundingBox.midX < $1.boundingBox.midX }
        let leftBoundary: CGFloat
        let rightBoundary: CGFloat
        if sortedWindows.count >= 2,
           let index = sortedWindows.firstIndex(where: { $0.text == windowLine.text && $0.boundingBox == windowLine.boundingBox }) {
            leftBoundary = index == 0 ? 0 : (sortedWindows[index - 1].boundingBox.midX + windowLine.boundingBox.midX) / 2
            rightBoundary = index == sortedWindows.count - 1 ? 1 : (windowLine.boundingBox.midX + sortedWindows[index + 1].boundingBox.midX) / 2
        } else {
            leftBoundary = max(0, windowLine.boundingBox.midX - 0.25)
            rightBoundary = min(1, windowLine.boundingBox.midX + 0.25)
        }

        return lines
            .filter { line in
                line.boundingBox.midX >= leftBoundary &&
                line.boundingBox.midX < rightBoundary &&
                line.boundingBox.midY <= windowLine.boundingBox.midY + 0.04 &&
                windowLine.boundingBox.midY - line.boundingBox.midY < 0.28
            }
            .sorted { left, right in
                let yDelta = abs(left.boundingBox.midY - right.boundingBox.midY)
                if yDelta > 0.012 {
                    return left.boundingBox.midY > right.boundingBox.midY
                }
                return left.boundingBox.midX < right.boundingBox.midX
            }
    }

    private static func remainingValueNearLabel(lines: [OCRLine]) -> Int? {
        let labelLine = lines.first { $0.text.contains("可调度剩余") }
        guard let labelLine else { return nil }
        if let value = remainingNumber(in: labelLine.text) {
            return plausibleRemaining(value)
        }

        return lines
            .filter { candidate in
                candidate.boundingBox.midY < labelLine.boundingBox.midY &&
                labelLine.boundingBox.midY - candidate.boundingBox.midY < 0.12 &&
                abs(candidate.boundingBox.midX - labelLine.boundingBox.midX) < 0.18
            }
            .sorted { left, right in
                let leftDistance = abs(left.boundingBox.midX - labelLine.boundingBox.midX) + abs(left.boundingBox.midY - labelLine.boundingBox.midY)
                let rightDistance = abs(right.boundingBox.midX - labelLine.boundingBox.midX) + abs(right.boundingBox.midY - labelLine.boundingBox.midY)
                return leftDistance < rightDistance
            }
            .compactMap { plausibleRemaining(remainingNumber(in: $0.text)) }
            .first
    }

    private static func remainingValueInWindowColumn(lines: [OCRLine]) -> Int? {
        let values = remainingNumbersInWindowArea(lines.map(\.text))
            .compactMap { plausibleRemaining($0) }
        return values.first
    }

    private static func positionedLine(containing label: String, in lines: [OCRLine]) -> OCRLine? {
        lines.first { $0.text.replacingOccurrences(of: " ", with: "").contains(label) }
    }

    private static func plausibleRemaining(_ value: Int?) -> Int? {
        guard let value, value >= 100 else { return nil }
        return value
    }

    private static func remainingValue(windowLabel: String, lines: [String]) -> Int? {
        if let value = remainingValueByWindowOrder(windowLabel: windowLabel, lines: lines) {
            return value
        }

        guard let windowIndex = lines.firstIndex(where: { line in
            let compact = line.replacingOccurrences(of: " ", with: "").lowercased()
            return compact.contains(windowLabel.lowercased()) && compact.contains("窗口")
        }) else { return nil }

        let windowLines = Array(lines.dropFirst(windowIndex).prefix(14))
        for (index, line) in windowLines.enumerated() where line.contains("可调度剩余") {
            if let value = remainingNumber(in: line) {
                return value
            }
            for nextLine in windowLines.dropFirst(index + 1).prefix(4) {
                if let value = remainingNumber(in: nextLine) {
                    return value
                }
            }
        }
        return nil
    }

    private static func remainingValueByWindowOrder(windowLabel: String, lines: [String]) -> Int? {
        let labels = windowLabelPositions(lines: lines)
        guard let label = labels.first(where: { $0.label == windowLabel.lowercased() }) else { return nil }

        let nextLabelLine = labels
            .filter { $0.line > label.line }
            .map(\.line)
            .min() ?? lines.count
        let windowArea = Array(lines[label.line..<nextLabelLine])
        let values = remainingNumbersInWindowArea(windowArea)
        return values.first
    }

    private static func windowLabelPositions(lines: [String]) -> [(line: Int, column: Int, label: String)] {
        var labels: [(line: Int, column: Int, label: String)] = []
        for (lineIndex, line) in lines.enumerated() {
            let compact = line.replacingOccurrences(of: " ", with: "").lowercased()
            for label in ["5h", "7d"] where compact.contains(label) && compact.contains("窗口") {
                let column = (compact as NSString).range(of: label).location
                labels.append((lineIndex, column == NSNotFound ? 0 : column, label))
            }
        }
        return labels
            .sorted { left, right in
                if left.line == right.line {
                    return left.column < right.column
                }
                return left.line < right.line
            }
    }

    private static func remainingNumber(in text: String) -> Int? {
        let compact = text
            .replacingOccurrences(of: " ", with: "")
            .replacingOccurrences(of: ",", with: "")

        if let value = firstDecimalInt(in: compact, pattern: #"余([0-9]{1,6}(?:\.[0-9]+)?)"#) {
            return value
        }
        if compact.contains("可调度剩余"),
           let value = firstDecimalInt(in: compact, pattern: #"可调度剩余([0-9]{1,6}(?:\.[0-9]+)?)"#) {
            return value
        }
        if compact.contains("/") {
            let decimalValues = decimalInts(in: compact).filter { $0.hasDecimal && !$0.isPercent }
            return decimalValues.last?.value
        }
        if isStandaloneRemainingNumber(compact) {
            return firstDecimalInt(in: compact)
        }
        return nil
    }

    private static func firstDecimalInt(in text: String, pattern: String) -> Int? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)),
              match.numberOfRanges > 1 else { return nil }
        let value = (text as NSString).substring(with: match.range(at: 1))
        guard let number = Double(value) else { return nil }
        return Int(number.rounded())
    }

    private static func remainingNumbersInWindowArea(_ lines: [String]) -> [Int] {
        var values: [Int] = []
        for line in lines {
            let compact = line
                .replacingOccurrences(of: " ", with: "")
                .replacingOccurrences(of: ",", with: "")

            if compact.contains("余") {
                values.append(contentsOf: decimalIntsAfterRemainingMarker(in: compact))
                continue
            }

            if compact.contains("可调度剩余") {
                if let inlineValue = firstDecimalInt(in: compact, pattern: #"可调度剩余([0-9]{1,6}(?:\.[0-9]+)?)"#) {
                    values.append(inlineValue)
                }
                continue
            }

            if compact.contains("/") {
                values.append(contentsOf: decimalInts(in: compact)
                    .filter { $0.hasDecimal && !$0.isPercent }
                    .map(\.value))
                continue
            }

            if isStandaloneRemainingNumber(compact), let value = firstDecimalInt(in: compact) {
                values.append(value)
            }
        }
        return values
    }

    private static func decimalIntsAfterRemainingMarker(in text: String) -> [Int] {
        let pattern = #"余([0-9]{1,6}(?:\.[0-9]+)?)"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let nsText = text as NSString
        return regex.matches(in: text, range: NSRange(location: 0, length: nsText.length)).compactMap { match in
            guard match.numberOfRanges > 1 else { return nil }
            let rawValue = nsText.substring(with: match.range(at: 1))
            guard let number = Double(rawValue) else { return nil }
            return Int(number.rounded())
        }
    }

    private static func decimalInts(in text: String) -> [(value: Int, hasDecimal: Bool, isPercent: Bool)] {
        let pattern = #"([0-9]{1,6}(?:\.[0-9]+)?)"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let nsText = text as NSString
        return regex.matches(in: text, range: NSRange(location: 0, length: nsText.length)).compactMap { match in
            guard match.numberOfRanges > 1 else { return nil }
            let rawValue = nsText.substring(with: match.range(at: 1))
            guard let number = Double(rawValue) else { return nil }
            let nextIndex = NSMaxRange(match.range(at: 1))
            let isPercent = nextIndex < nsText.length && nsText.substring(with: NSRange(location: nextIndex, length: 1)) == "%"
            return (Int(number.rounded()), rawValue.contains("."), isPercent)
        }
    }

    private static func isStandaloneRemainingNumber(_ text: String) -> Bool {
        guard let number = Double(text), number >= 100 else { return false }
        guard let regex = try? NSRegularExpression(pattern: #"^[0-9]{1,6}\.[0-9]+$"#) else { return false }
        return regex.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)) != nil
    }

    private static func integerOnly(_ text: String) -> Int? {
        let compact = text
            .replacingOccurrences(of: ",", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let regex = try? NSRegularExpression(pattern: #"^[0-9]+$"#),
              regex.firstMatch(in: compact, range: NSRange(location: 0, length: (compact as NSString).length)) != nil else {
            return nil
        }
        return Int(compact)
    }

    private var ownerCost: Double {
        max(Double(costField.stringValue.replacingOccurrences(of: ",", with: "")) ?? 0, 0)
    }

    private var partnerCost: Double {
        max(Double(partnerCostField.stringValue.replacingOccurrences(of: ",", with: "")) ?? 0, 0)
    }

    private func moneyInputValue(_ field: NSTextField) -> Double? {
        let raw = field.stringValue
            .replacingOccurrences(of: ",", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty, let value = Double(raw), value >= 0 else { return nil }
        return value
    }

    private var effectiveWithdrawalAmount: Double {
        moneyInputValue(withdrawalField) ?? withdrawalAmount ?? history.last?.total ?? 0
    }

    private func roundedWithdrawalAmount(_ value: Double) -> Double {
        floor(max(value, 0) / 100) * 100
    }

    private var effectiveBaseDeduction: Double {
        guard useBaseDeduction else { return 0 }
        return moneyInputValue(baseDeductionField) ?? baseDeductionAmount ?? 0
    }

    private var cost: Double {
        ownerCost + partnerCost
    }

    private func refreshOutput() {
        updateMetrics()
        updateSettlementOutput()
        updatePoolSummaryLabels()
        trendChartView.history = poolHistory
        trendChartView.groups = selectedPoolGroups
        trendChartView.metric = selectedTrendMetric
        balanceTrendChartView.history = history
        comparisonTable.reloadData()
        historyTable.reloadData()
        plusPoolHistoryTable.reloadData()
        k12PoolHistoryTable.reloadData()
        for table in poolHistoryTables.keys {
            table.reloadData()
        }
        scrollToLatestRows()
    }

    private func scrollToLatestRows() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
            guard let self else { return }
            let tables = [self.comparisonTable, self.historyTable, self.plusPoolHistoryTable, self.k12PoolHistoryTable] + Array(self.poolHistoryTables.keys)
            for table in tables {
                self.smoothScrollToLastRow(table)
            }
        }
    }

    private func smoothScrollToLastRow(_ table: NSTableView) {
        let lastRow = table.numberOfRows - 1
        guard lastRow >= 0 else { return }
        table.layoutSubtreeIfNeeded()
        guard let scrollView = table.enclosingScrollView else {
            table.scrollRowToVisible(lastRow)
            return
        }
        let clipView = scrollView.contentView
        let rowRect = table.rect(ofRow: lastRow)
        let maxY = max(table.bounds.height - clipView.bounds.height, 0)
        let targetY = min(max(rowRect.maxY - clipView.bounds.height + 6, 0), maxY)
        var targetOrigin = clipView.bounds.origin
        targetOrigin.y = targetY
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.28
            context.allowsImplicitAnimation = true
            clipView.animator().setBoundsOrigin(targetOrigin)
        } completionHandler: {
            scrollView.reflectScrolledClipView(clipView)
        }
    }

    private func appendPoolSnapshot(_ snapshot: PoolSnapshot) {
        poolHistory.append(snapshot)
        trimPoolHistory()
        savePoolState()
        finishPaste(success: true)
        showFeedback("已识别 \(snapshot.groupName)：总账号 \(snapshot.total)，并发可用 \(snapshot.concurrentAvailable)/\(snapshot.concurrentTotal)。", color: .systemGreen)
        refreshOutput()
    }

    private func poolRows(for tableView: NSTableView) -> [PoolSnapshot] {
        let groupFilter = poolHistoryTables[tableView] ?? (tableView == k12PoolHistoryTable ? "K12共享号池" : "PLUS共享号池")
        return poolHistory
            .filter { $0.groupName == groupFilter }
            .sorted { $0.date < $1.date }
    }

    private func trimBalanceHistory() {
        guard history.count > maxBalanceHistoryCount else { return }
        history = Array(history.sorted { $0.date < $1.date }.suffix(maxBalanceHistoryCount))
        if let initial {
            history.removeAll { $0.date == initial.date }
            history.insert(initial, at: 0)
        }
    }

    private func trimPoolHistory() {
        var trimmed: [PoolSnapshot] = []
        for group in Set(poolHistory.map(\.groupName)) {
            let rows = poolHistory
                .filter { $0.groupName == group }
                .sorted { $0.date < $1.date }
                .suffix(maxPoolHistoryPerGroup)
            trimmed.append(contentsOf: rows)
        }
        poolHistory = trimmed.sorted { $0.date < $1.date }
    }

    private func latestPoolSnapshot(groupName: String) -> PoolSnapshot? {
        poolHistory.filter { $0.groupName == groupName }.max { $0.date < $1.date }
    }

    private func previousPoolSnapshot(before snapshot: PoolSnapshot) -> PoolSnapshot? {
        poolHistory
            .filter { $0.groupName == snapshot.groupName && $0.date < snapshot.date }
            .max { $0.date < $1.date }
    }

    private func poolDeltaText(for snapshot: PoolSnapshot) -> String {
        guard let previous = previousPoolSnapshot(before: snapshot) else {
            return "暂无上次对比"
        }
        let delta = snapshot.total - previous.total
        if delta > 0 {
            return "新增 \(delta) 个账号"
        }
        if delta < 0 {
            return "减少 \(abs(delta)) 个账号"
        }
        return "账号数无变化"
    }

    private func signedCount(_ value: Int) -> String {
        value > 0 ? "+\(value)" : "\(value)"
    }

    private func setTrendCell(_ cell: NSTextField, current: Int, previous: Int?) {
        guard let previous else {
            cell.stringValue = "\(current)"
            cell.textColor = .labelColor
            return
        }
        let delta = current - previous
        guard delta != 0 else {
            cell.stringValue = "\(current)"
            cell.textColor = .labelColor
            return
        }
        let marker = delta > 0 ? "↑\(delta)" : "↓\(abs(delta))"
        setMixedTrendCell(cell, base: "\(current)", marker: marker, delta: delta)
    }

    private func setConcurrentTrendCell(_ cell: NSTextField, current: PoolSnapshot, previous: PoolSnapshot?) {
        let base = "\(current.concurrentAvailable)/\(current.concurrentTotal)"
        guard let previous else {
            cell.stringValue = base
            cell.textColor = .labelColor
            return
        }
        let delta = current.concurrentAvailable - previous.concurrentAvailable
        guard delta != 0 else {
            cell.stringValue = base
            cell.textColor = .labelColor
            return
        }
        let marker = delta > 0 ? "↑\(delta)" : "↓\(abs(delta))"
        setMixedTrendCell(cell, base: base, marker: marker, delta: delta)
    }

    private func quotaWindowCell(remaining: Int?, utilization: Double?, previous: Int?) -> NSView {
        let container = NSView()
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 4
        stack.alignment = .width
        stack.translatesAutoresizingMaskIntoConstraints = false

        let topRow = NSStackView()
        topRow.orientation = .horizontal
        topRow.spacing = 6
        topRow.alignment = .centerY

        let remainingLabel = NSTextField(labelWithString: "--")
        remainingLabel.font = .systemFont(ofSize: 12, weight: .semibold)
        remainingLabel.textColor = .labelColor
        remainingLabel.alignment = .left
        remainingLabel.lineBreakMode = .byTruncatingTail

        if let remaining {
            if let previous, previous != remaining {
                let delta = remaining - previous
                let marker = delta > 0 ? "↑\(delta)" : "↓\(abs(delta))"
                let fullText = "剩余数量：\(remaining) \(marker)"
                let attributed = NSMutableAttributedString(
                    string: fullText,
                    attributes: [
                        .font: remainingLabel.font ?? NSFont.systemFont(ofSize: 12, weight: .semibold),
                        .foregroundColor: NSColor.labelColor
                    ]
                )
                attributed.addAttribute(
                    .foregroundColor,
                    value: delta > 0 ? NSColor.systemGreen : NSColor.systemRed,
                    range: (fullText as NSString).range(of: marker)
                )
                remainingLabel.attributedStringValue = attributed
            } else {
                remainingLabel.stringValue = "剩余数量：\(remaining)"
            }
        } else {
            remainingLabel.stringValue = "剩余数量：--"
            remainingLabel.textColor = .secondaryLabelColor
        }

        let percentValue = utilization.map { max(0, min($0 / 100.0, 1)) }
        let percentLabel = NSTextField(labelWithString: percentValue.map { String(format: "%.1f%%", $0 * 100) } ?? "--")
        percentLabel.font = .systemFont(ofSize: 11, weight: .medium)
        percentLabel.textColor = .secondaryLabelColor
        percentLabel.alignment = .right
        percentLabel.widthAnchor.constraint(equalToConstant: 48).isActive = true

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        topRow.addArrangedSubview(remainingLabel)
        topRow.addArrangedSubview(spacer)
        topRow.addArrangedSubview(percentLabel)

        let progress = UsageBarView()
        progress.value = percentValue ?? 0
        progress.translatesAutoresizingMaskIntoConstraints = false
        progress.heightAnchor.constraint(equalToConstant: 8).isActive = true
        progress.isHidden = percentValue == nil

        stack.addArrangedSubview(topRow)
        stack.addArrangedSubview(progress)
        container.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 8),
            stack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -8),
            stack.centerYAnchor.constraint(equalTo: container.centerYAnchor)
        ])
        return container
    }

    private func setMixedTrendCell(_ cell: NSTextField, base: String, marker: String, delta: Int) {
        let fullText = "\(base) \(marker)"
        let attributed = NSMutableAttributedString(
            string: fullText,
            attributes: [
                .font: cell.font ?? NSFont.systemFont(ofSize: 13),
                .foregroundColor: NSColor.labelColor
            ]
        )
        let markerRange = (fullText as NSString).range(of: marker)
        attributed.addAttribute(
            .foregroundColor,
            value: delta > 0 ? NSColor.systemGreen : NSColor.systemRed,
            range: markerRange
        )
        cell.attributedStringValue = attributed
    }

    private func setPlainTrendCell(_ cell: NSTextField, text: String, delta: Int) {
        cell.stringValue = text
        cell.textColor = delta > 0 ? .systemGreen : .systemRed
    }

    private func updatePoolSummaryLabels() {
        for groupName in ["PLUS共享号池", "K12共享号池"] {
            guard let labels = poolSummaryLabels[groupName] else { continue }
            guard let latest = latestPoolSnapshot(groupName: groupName) else {
                labels.total.stringValue = "总账号 --"
                labels.schedulable.stringValue = "可调度 --"
                labels.status.stringValue = "状态 --"
                labels.change.stringValue = "暂无对比"
                labels.change.textColor = .secondaryLabelColor
                labels.time.stringValue = "暂无历史"
                continue
            }
            setPoolIntText(key: "\(groupName).total", field: labels.total, prefix: "总账号 ", value: latest.total)
            setPoolIntText(key: "\(groupName).schedulable", field: labels.schedulable, prefix: "可调度 ", value: latest.schedulable)
            setPoolStatusText(groupName: groupName, field: labels.status, snapshot: latest)
            labels.change.stringValue = poolDeltaText(for: latest)
            let delta = previousPoolSnapshot(before: latest).map { latest.total - $0.total } ?? 0
            labels.change.textColor = delta > 0 ? .systemGreen : (delta < 0 ? .systemRed : .secondaryLabelColor)
            labels.time.stringValue = "最新记录：\(formatDate(latest.date))"
        }
    }

    private func setPoolIntText(key: String, field: NSTextField, prefix: String, value: Int) {
        poolAnimations[key]?.invalidate()
        let startValue = displayedPoolValues[key] ?? value
        displayedPoolValues[key] = value
        guard startValue != value else {
            field.stringValue = "\(prefix)\(value)"
            return
        }

        let steps = 20
        let interval = 0.018
        var step = 0
        poolAnimations[key] = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self, weak field] timer in
            guard let self, let field else {
                timer.invalidate()
                return
            }
            step += 1
            let rawProgress = min(Double(step) / Double(steps), 1)
            let eased = 1 - pow(1 - rawProgress, 3)
            let current = Int((Double(startValue) + Double(value - startValue) * eased).rounded())
            field.stringValue = "\(prefix)\(current)"
            if step >= steps {
                field.stringValue = "\(prefix)\(value)"
                timer.invalidate()
                self.poolAnimations.removeValue(forKey: key)
            }
        }
    }

    private func setPoolStatusText(groupName: String, field: NSTextField, snapshot: PoolSnapshot) {
        let keysAndValues: [(String, String, Int)] = [
            ("limited", "限流", snapshot.limited),
            ("quotaProtected", "额度保护", snapshot.quotaProtected),
            ("error", "错误", snapshot.error),
            ("disabled", "禁用", snapshot.disabled)
        ]
        let animationKey = "\(groupName).status"
        poolAnimations[animationKey]?.invalidate()

        let starts = keysAndValues.map { item in
            displayedPoolValues["\(groupName).\(item.0)"] ?? item.2
        }
        let targets = keysAndValues.map { $0.2 }
        for item in keysAndValues {
            displayedPoolValues["\(groupName).\(item.0)"] = item.2
        }

        guard starts != targets else {
            setPoolStatusAttributedText(field, status: snapshot.status, values: keysAndValues.map { ($0.1, $0.2) })
            return
        }

        let steps = 20
        let interval = 0.018
        var step = 0
        poolAnimations[animationKey] = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self, weak field] timer in
            guard let self, let field else {
                timer.invalidate()
                return
            }
            step += 1
            let rawProgress = min(Double(step) / Double(steps), 1)
            let eased = 1 - pow(1 - rawProgress, 3)
            let values = keysAndValues.enumerated().map { index, item in
                let current = Int((Double(starts[index]) + Double(targets[index] - starts[index]) * eased).rounded())
                return (item.1, current)
            }
            self.setPoolStatusAttributedText(field, status: snapshot.status, values: values)
            if step >= steps {
                self.setPoolStatusAttributedText(field, status: snapshot.status, values: keysAndValues.map { ($0.1, $0.2) })
                timer.invalidate()
                self.poolAnimations.removeValue(forKey: animationKey)
            }
        }
    }

    private func poolStatusLine(status: String, values: [(String, Int)]) -> String {
        let metrics = values.map { "\($0.0) \($0.1)" }.joined(separator: " · ")
        return "状态 \(status) · \(metrics)"
    }

    private func setPoolStatusAttributedText(_ field: NSTextField, status: String, values: [(String, Int)]) {
        let text = poolStatusLine(status: status, values: values)
        let attributed = NSMutableAttributedString(
            string: text,
            attributes: [
                .font: field.font ?? NSFont.systemFont(ofSize: 13),
                .foregroundColor: NSColor.secondaryLabelColor
            ]
        )
        let nsText = text as NSString
        let statusRange = nsText.range(of: status)
        if statusRange.location != NSNotFound {
            attributed.addAttribute(.foregroundColor, value: poolStatusColor(status), range: statusRange)
        }
        for (label, value) in values {
            let token = "\(label) \(value)"
            let range = nsText.range(of: token)
            if range.location != NSNotFound {
                attributed.addAttribute(.foregroundColor, value: poolMetricColor(label), range: range)
            }
        }
        field.attributedStringValue = attributed
    }

    private func poolStatusColor(_ status: String) -> NSColor {
        if status.contains("正常") {
            return .systemGreen
        }
        if status.contains("资源紧张") {
            return .systemOrange
        }
        return .secondaryLabelColor
    }

    private func poolMetricColor(_ label: String) -> NSColor {
        switch label {
        case "限流":
            return .systemOrange
        case "额度保护":
            return .systemYellow
        case "错误":
            return .systemRed
        case "禁用":
            return .systemGray
        default:
            return .secondaryLabelColor
        }
    }

    private func updateMetrics() {
        setMetric("cost", field: costValue, value: cost, suffix: " 元")
        guard let latestTotal = history.last?.total else {
            setMetric("current", field: currentValue, value: nil, suffix: " 元")
            setMetric("net", field: netValue, value: nil, suffix: " 元")
            setMetric("result", field: resultValue, value: nil, suffix: " 元")
            setMetricText("remaining", field: remainingValue, text: "--")
            return
        }
        let bookProfit = latestTotal - effectiveBaseDeduction - cost
        setMetric("current", field: currentValue, value: latestTotal, suffix: " 元")
        setMetric("net", field: netValue, value: bookProfit, suffix: " 元")
        netValue.textColor = bookProfit >= 0 ? .systemGreen : .systemRed
        setMetric("result", field: resultValue, value: bookProfit, suffix: " 元")
        resultValue.textColor = bookProfit >= 0 ? .systemGreen : .systemRed
        animateMetricNumber("remaining", field: remainingValue, value: bookProfit) { "\(String(format: "%+.2f", $0)) 元" }
        remainingValue.textColor = bookProfit >= 0 ? .systemGreen : .systemRed
    }

    private func setMetricText(_ key: String, field: NSTextField, text: String) {
        metricAnimations[key]?.invalidate()
        displayedMetricValues.removeValue(forKey: key)
        field.stringValue = text
        resizeMetricField(field)
    }

    private func setMetric(_ key: String, field: NSTextField, value: Double?, suffix: String, decimals: Int = 2) {
        metricAnimations[key]?.invalidate()

        guard let value else {
            displayedMetricValues.removeValue(forKey: key)
            field.stringValue = "--"
            resizeMetricField(field)
            return
        }

        animateMetricNumber(key, field: field, value: value) { self.formatMetric($0, suffix: suffix, decimals: decimals) }
    }

    private func animateMetricNumber(_ key: String, field: NSTextField, value: Double, formatter: @escaping (Double) -> String) {
        metricAnimations[key]?.invalidate()

        let startValue = displayedMetricValues[key] ?? value
        displayedMetricValues[key] = value

        guard startValue != value else {
            field.stringValue = formatter(value)
            resizeMetricField(field)
            return
        }

        let duration = 1.5
        let interval = 1.0 / 60.0
        let steps = max(1, Int(duration / interval))
        var step = 0
        metricAnimations[key] = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self, weak field] timer in
            guard let self, let field else {
                timer.invalidate()
                return
            }
            step += 1
            let rawProgress = min(Double(step) / Double(steps), 1)
            let eased = 1 - pow(1 - rawProgress, 3)
            let current = startValue + (value - startValue) * eased
            field.stringValue = formatter(current)
            self.resizeMetricField(field)
            if step >= steps {
                field.stringValue = formatter(value)
                self.resizeMetricField(field)
                self.metricAnimations.removeValue(forKey: key)
                timer.invalidate()
            }
        }
    }

    private func formatMetric(_ value: Double, suffix: String, decimals: Int) -> String {
        "\(String(format: "%.\(decimals)f", value))\(suffix)"
    }

    private func resizeMetricField(_ field: NSTextField) {
        field.sizeToFit()
        field.frame = NSRect(x: 0, y: 0, width: max(field.frame.width + 8, 92), height: 25)
    }

    func numberOfRows(in tableView: NSTableView) -> Int {
        if tableView == plusPoolHistoryTable || tableView == k12PoolHistoryTable || poolHistoryTables[tableView] != nil {
            return poolRows(for: tableView).count
        }
        if tableView == comparisonTable {
            guard let initial, let latest = history.last else { return 0 }
            return max(initial.amounts.count, latest.amounts.count)
        }
        if tableView == historyTable {
            return history.count
        }
        return 0
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard let identifier = tableColumn?.identifier.rawValue else { return nil }
        let container = NSView()
        let cell = NSTextField(labelWithString: "")
        cell.font = .systemFont(ofSize: 13)
        cell.alignment = .center
        cell.lineBreakMode = .byTruncatingTail
        cell.maximumNumberOfLines = 1
        cell.cell?.wraps = false
        cell.cell?.isScrollable = true
        cell.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(cell)
        NSLayoutConstraint.activate([
            cell.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            cell.centerYAnchor.constraint(equalTo: container.centerYAnchor),
            cell.leadingAnchor.constraint(greaterThanOrEqualTo: container.leadingAnchor, constant: 6),
            cell.trailingAnchor.constraint(lessThanOrEqualTo: container.trailingAnchor, constant: -6)
        ])

        if tableView == plusPoolHistoryTable || tableView == k12PoolHistoryTable || poolHistoryTables[tableView] != nil {
            let rows = poolRows(for: tableView)
            guard row < rows.count else { return container }
            let item = rows[row]
            let previous = row > 0 ? rows[row - 1] : nil
            switch identifier {
            case "time":
                cell.stringValue = formatDate(item.date)
            case "total":
                setTrendCell(cell, current: item.total, previous: previous?.total)
            case "remaining5h":
                return quotaWindowCell(remaining: item.remaining5h, utilization: item.utilization5h, previous: previous?.remaining5h)
            case "remaining7d":
                return quotaWindowCell(remaining: item.remaining7d, utilization: item.utilization7d, previous: previous?.remaining7d)
            case "concurrent":
                setConcurrentTrendCell(cell, current: item, previous: previous)
            case "limited":
                setTrendCell(cell, current: item.limited, previous: previous?.limited)
            case "quotaProtected":
                setTrendCell(cell, current: item.quotaProtected, previous: previous?.quotaProtected)
            case "error":
                setTrendCell(cell, current: item.error, previous: previous?.error)
            case "disabled":
                setTrendCell(cell, current: item.disabled, previous: previous?.disabled)
            case "delta":
                if let previous {
                    let delta = item.total - previous.total
                    if delta == 0 {
                        cell.stringValue = "无变化"
                        cell.textColor = .secondaryLabelColor
                    } else {
                        setPlainTrendCell(cell, text: signedCount(delta), delta: delta)
                    }
                } else {
                    cell.stringValue = "首次记录"
                    cell.textColor = .secondaryLabelColor
                }
            default:
                break
            }
            return container
        }

        if tableView == comparisonTable {
            guard let initial, let latest = history.last else { return container }
            let end = row < latest.amounts.count ? latest.amounts[row] : 0
            let accounts = initial.accounts ?? latest.accounts ?? []
            let account = row < accounts.count ? accounts[row] : "账号 \(row + 1)"
            switch identifier {
            case "index":
                cell.stringValue = "\(row + 1)"
            case "account":
                cell.stringValue = account
            case "current":
                cell.stringValue = money(end)
            default:
                break
            }
            return container
        }

        if tableView == historyTable {
            let item = history[row]
            let gross = item.total
            let afterCost = gross - cost
            switch identifier {
            case "index":
                cell.stringValue = "\(row + 1)"
            case "time":
                cell.stringValue = formatDate(item.date)
            case "total":
                cell.stringValue = money(item.total)
            case "gross":
                cell.stringValue = signedMoney(gross)
                cell.textColor = gross >= 0 ? .systemGreen : .systemRed
            case "afterCost":
                cell.stringValue = signedMoney(afterCost)
                cell.textColor = afterCost >= 0 ? .systemGreen : .systemRed
            default:
                break
            }
            return container
        }

        return container
    }

    private func intervalComparePercent(row: Int) -> Double? {
        guard let initial, history.count >= 2 else { return nil }
        let latest = history[history.count - 1]
        let previous = history[history.count - 2]
        let beforePrevious = history.count >= 3 ? history[history.count - 3] : initial

        let currentInterval = amount(at: row, in: latest) - amount(at: row, in: previous)
        let previousInterval = amount(at: row, in: previous) - amount(at: row, in: beforePrevious)
        guard previousInterval != 0 else { return nil }
        return (currentInterval - previousInterval) / abs(previousInterval) * 100
    }

    private func amount(at index: Int, in snapshot: Snapshot) -> Double {
        index < snapshot.amounts.count ? snapshot.amounts[index] : 0
    }

    private func buildReport() -> String {
        var lines: [String] = []
        lines.append("总出资：星星 \(money(ownerCost)) 元 + 社会哥 \(money(partnerCost)) 元 = \(money(cost)) 元")
        lines.append("")

        let withdrawal = effectiveWithdrawalAmount
        let baseDeduction = effectiveBaseDeduction
        let gross = withdrawal - baseDeduction
        let currentProfitAfterCost = gross - cost
        let remaining = currentProfitAfterCost
        lines.append("提现结算")
        lines.append("  本次提现金额：\(money(withdrawal)) 元")
        if baseDeduction > 0 {
            lines.append("  扣基准余额：\(money(baseDeduction)) 元")
            lines.append("  分成基数：\(money(gross)) 元")
        }
        lines.append("")
        lines.append("当前结果")
        lines.append("  当前提现利润：\(money(currentProfitAfterCost)) 元")
        lines.append("")
        lines.append("回本状态：\(signedMoney(remaining)) 元")

        return lines.joined(separator: "\n")
    }

    private func buildHistoryReport() -> String {
        guard !history.isEmpty else {
            return "暂无历史记录。复制最新截图后按 Command+V，会自动追加到这里。"
        }

        var rows = [
            "┌──────┬──────────┬──────────────┬──────────────┬──────────────┬──────────────┐",
            "│ 序号 │ 时间     │ 余额合计(元) │ 扣成本后     │ 总利润       │",
            "├──────┼──────────┼──────────────┼──────────────┼──────────────┤"
        ]

        for (index, item) in history.enumerated() {
            let gross = item.total
            let afterCost = gross - cost
            rows.append(String(format: "│ %4d │ %-8@ │ %12.2f │ %+12.2f │ %+12.2f │",
                               index + 1,
                               formatDate(item.date) as NSString,
                               item.total,
                               gross,
                               afterCost))
        }

        rows.append("└──────┴──────────┴──────────────┴──────────────┴──────────────┘")
        return rows.joined(separator: "\n")
    }

    private func amountList(_ amounts: [Double]) -> String {
        amounts.map { money($0) }.joined(separator: " + ")
    }

    private func money(_ value: Double) -> String {
        String(format: "%.2f", value)
    }

    private func signedMoney(_ value: Double) -> String {
        String(format: "%+.2f", value)
    }

    private func percent(_ value: Double) -> String {
        String(format: "%.1f%%", value)
    }

    private func signedPercent(_ value: Double) -> String {
        String(format: "%+.1f%%", value)
    }

    private func formatDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.timeZone = .current
        formatter.dateFormat = "MM-dd HH:mm:ss"
        return formatter.string(from: date)
    }

    private func showError(_ message: String) {
        feedbackHideTimer?.invalidate()
        statusLabel.stringValue = message.count > 120 ? String(message.prefix(120)) + "..." : message
        statusLabel.textColor = .systemRed
        feedbackBar?.isHidden = false
        showDetailDialog(title: "处理失败", message: message, style: .warning)
    }

    private func showDetailDialog(title: String, message: String, style: NSAlert.Style) {
        let alert = NSAlert()
        alert.messageText = title
        alert.alertStyle = style
        alert.addButton(withTitle: "关闭")

        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 640, height: 220))
        textView.string = message
        textView.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.isEditable = false
        textView.isSelectable = true
        textView.textContainerInset = NSSize(width: 8, height: 8)

        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 660, height: 240))
        scroll.documentView = textView
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .lineBorder
        alert.accessoryView = scroll
        alert.runModal()
    }

    private func serverEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    private func serverDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    private func currentStoredState() -> StoredState {
        StoredState(
            cost: ownerCost,
            partnerCost: partnerCost,
            manualBaseTotal: nil,
            withdrawalAmount: moneyInputValue(withdrawalField) ?? withdrawalAmount,
            useBaseDeduction: useBaseDeduction,
            baseDeductionAmount: moneyInputValue(baseDeductionField) ?? baseDeductionAmount,
            initial: initial,
            history: history,
            costAdditions: costAdditions,
            settlement: settlement
        )
    }

    private func currentPoolState() -> PoolAnalyzerState {
        PoolAnalyzerState(
            history: poolHistory,
            selectedGroups: selectedPoolGroups,
            availableGroups: availablePoolGroups,
            pollingMinutes: poolPollingMinutes,
            warningEmail: smtpSettings.recipient,
            accessToken: poolAccessToken,
            refreshToken: poolRefreshToken
        )
    }

    private func setTextFieldFromServer(_ field: NSTextField, value: String) {
        guard field.currentEditor() == nil else { return }
        if field.stringValue != value {
            field.stringValue = value
        }
    }

    private func applyServerState(_ response: ServerStateResponse, manual: Bool) {
        serverInitialized = response.initialized
        UserDefaults.standard.set(response.initialized, forKey: "ServerInitialized")
        if let state = response.storedState {
            setTextFieldFromServer(costField, value: money(state.cost))
            setTextFieldFromServer(partnerCostField, value: money(state.partnerCost ?? 0))
            withdrawalAmount = state.withdrawalAmount
            useBaseDeduction = state.useBaseDeduction ?? false
            baseDeductionAmount = state.baseDeductionAmount ?? state.manualBaseTotal
            setTextFieldFromServer(withdrawalField, value: state.withdrawalAmount.map { money($0) } ?? "")
            setTextFieldFromServer(baseDeductionField, value: baseDeductionAmount.map { money($0) } ?? "")
            baseDeductionField.isHidden = !useBaseDeduction
            useBaseDeductionButton.state = useBaseDeduction ? .on : .off
            initial = state.initial
            history = state.history
            costAdditions = state.costAdditions ?? []
            settlement = state.settlement ?? .default
            saveState()
        }
        if let state = response.poolState {
            poolHistory = state.history
            if !hasLocalPoolSelection {
                selectedPoolGroups = state.selectedGroups ?? selectedPoolGroups
            }
            availablePoolGroups = state.availableGroups ?? availablePoolGroups
            poolPollingMinutes = normalizedPollingMinutes(state.pollingMinutes ?? poolPollingMinutes)
            poolPollingField.stringValue = money(poolPollingMinutes)
            updatePoolGroupsLabel()
            savePoolState()
        }
        refreshOutput()
        if manual {
            showFeedback(response.initialized ? "已从服务器同步最新数据。" : "服务器还未初始化，请先上传服务器。", color: .systemGreen)
        }
    }

    private func fetchServerState(manual: Bool = false) {
        guard let url = URL(string: "\(analyzerServerBaseURL)/state") else { return }
        URLSession.shared.dataTask(with: url) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                if let error {
                    if manual { self.showError("服务器状态读取失败：\(error.localizedDescription)") }
                    return
                }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard (200..<300).contains(statusCode), let data else {
                    if manual { self.showError("服务器状态读取失败：HTTP \(statusCode)") }
                    return
                }
                do {
                    let decoded = try self.serverDecoder().decode(ServerStateResponse.self, from: data)
                    self.applyServerState(decoded, manual: manual)
                } catch {
                    if manual { self.showError("服务器状态解析失败：\(error.localizedDescription)") }
                }
            }
        }.resume()
    }

    private func refreshServerData(manual: Bool) {
        guard !serverSyncInProgress else {
            if manual { showFeedback("正在同步服务器，请稍等。", color: .systemOrange) }
            return
        }
        guard let url = URL(string: "\(analyzerServerBaseURL)/refresh") else { return }
        serverSyncInProgress = true
        if manual { showFeedback("正在请求服务器刷新数据...", color: .white, autoHide: false) }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.serverSyncInProgress = false
                if let error {
                    if manual { self.showError("服务器刷新失败：\(error.localizedDescription)") }
                    return
                }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard (200..<300).contains(statusCode), let data else {
                    if manual {
                        let message = statusCode == 409 ? "服务器还未初始化，请先上传服务器。" : "服务器刷新失败：HTTP \(statusCode)"
                        self.showError(message)
                    }
                    return
                }
                do {
                    let decoded = try self.serverDecoder().decode(ServerRefreshResponse.self, from: data)
                    if let state = decoded.state {
                        self.applyServerState(state, manual: manual)
                    } else {
                        self.fetchServerState(manual: manual)
                    }
                } catch {
                    if manual { self.showError("服务器刷新结果解析失败：\(error.localizedDescription)") }
                }
            }
        }.resume()
    }

    @objc private func uploadInitialStateToServer() {
        guard !serverInitialized else { return }
        guard !serverSyncInProgress else {
            showFeedback("正在同步服务器，请稍等。", color: .systemOrange)
            return
        }
        let alert = NSAlert()
        alert.messageText = "上传到服务器？"
        alert.informativeText = "这会把当前本机历史、余额账号配置和账号池接口账号作为服务器唯一初始化数据。成功后其他客户端只能读取，不能再上传覆盖。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "确认上传")
        alert.addButton(withTitle: "取消")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        guard let url = URL(string: "\(analyzerServerBaseURL)/bootstrap") else { return }

        let payload = ServerBootstrapPayload(
            storedState: currentStoredState(),
            poolState: currentPoolState(),
            balanceAccounts: balanceAccounts,
            poolCredentials: loadPoolCredentials(),
            smtpSettings: smtpSettings
        )
        guard let body = try? serverEncoder().encode(payload) else {
            showError("服务器初始化数据编码失败。")
            return
        }

        serverSyncInProgress = true
        showFeedback("正在上传服务器初始化数据...", color: .white, autoHide: false)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.serverSyncInProgress = false
                if let error {
                    self.showError("上传服务器失败：\(error.localizedDescription)")
                    return
                }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard (200..<300).contains(statusCode) else {
                    let message = statusCode == 409 ? "服务器已经初始化，不能再次上传覆盖。" : "上传服务器失败：HTTP \(statusCode)"
                    self.showError(message)
                    self.fetchServerState(manual: false)
                    return
                }
                self.serverInitialized = true
                UserDefaults.standard.set(true, forKey: "ServerInitialized")
                self.showFeedback("服务器初始化完成，上传按钮已隐藏。", color: .systemGreen)
                self.fetchServerState(manual: false)
            }
        }.resume()
    }

    private func syncCostAdditionToServer(_ addition: CostAddition) {
        guard serverInitialized else { return }
        guard let url = URL(string: "\(analyzerServerBaseURL)/cost-additions") else { return }
        guard let body = try? serverEncoder().encode(addition) else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                if let error {
                    self.showFeedback("累加成本已保存在本机；服务器同步失败：\(error.localizedDescription)", color: .systemOrange, autoHide: false)
                    return
                }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard (200..<300).contains(statusCode), let data else {
                    self.showFeedback("累加成本已保存在本机；服务器同步失败：HTTP \(statusCode)", color: .systemOrange, autoHide: false)
                    return
                }
                if let decoded = try? self.serverDecoder().decode(ServerRefreshResponse.self, from: data),
                   let state = decoded.state {
                    self.applyServerState(state, manual: false)
                }
            }
        }.resume()
    }

    private func clearServerCostAdditions() {
        guard serverInitialized else { return }
        guard let url = URL(string: "\(analyzerServerBaseURL)/cost-additions") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                if let error {
                    self.showFeedback("累加成本已在本机清空；服务器同步失败：\(error.localizedDescription)", color: .systemOrange, autoHide: false)
                    return
                }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard (200..<300).contains(statusCode), let data else {
                    self.showFeedback("累加成本已在本机清空；服务器同步失败：HTTP \(statusCode)", color: .systemOrange, autoHide: false)
                    return
                }
                if let decoded = try? self.serverDecoder().decode(ServerRefreshResponse.self, from: data),
                   let state = decoded.state {
                    self.applyServerState(state, manual: false)
                }
            }
        }.resume()
    }

    private func scheduleStoredStateSyncToServer() {
        guard serverInitialized else { return }
        storedStateSyncTimer?.invalidate()
        storedStateSyncTimer = Timer.scheduledTimer(withTimeInterval: 0.8, repeats: false) { [weak self] _ in
            self?.syncStoredStateToServer()
        }
    }

    private func syncStoredStateToServer() {
        guard serverInitialized else { return }
        guard let url = URL(string: "\(analyzerServerBaseURL)/stored-state") else { return }
        guard let body = try? serverEncoder().encode(currentStoredState()) else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                if let error {
                    self.showFeedback("本机输入已保存；服务器同步失败：\(error.localizedDescription)", color: .systemOrange, autoHide: false)
                    return
                }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard (200..<300).contains(statusCode), let data else {
                    self.showFeedback("本机输入已保存；服务器同步失败：HTTP \(statusCode)", color: .systemOrange, autoHide: false)
                    return
                }
                if let decoded = try? self.serverDecoder().decode(ServerRefreshResponse.self, from: data),
                   let state = decoded.state {
                    self.applyServerState(state, manual: false)
                }
            }
        }.resume()
    }

    private func saveState() {
        let state = currentStoredState()
        if let data = try? JSONEncoder().encode(state) {
            UserDefaults.standard.set(data, forKey: "StoredState")
        }
    }

    private func savePoolState() {
        let state = currentPoolState()
        if let data = try? JSONEncoder().encode(state) {
            UserDefaults.standard.set(data, forKey: "PoolAnalyzerState")
        }
    }

    private func loadState() {
        guard let data = UserDefaults.standard.data(forKey: "StoredState"),
              let state = try? JSONDecoder().decode(StoredState.self, from: data) else {
            return
        }
        costField.stringValue = money(state.cost)
        partnerCostField.stringValue = money(state.partnerCost ?? 0)
        withdrawalAmount = state.withdrawalAmount
        useBaseDeduction = state.useBaseDeduction ?? false
        baseDeductionAmount = state.baseDeductionAmount ?? state.manualBaseTotal
        withdrawalField.stringValue = state.withdrawalAmount.map { money($0) } ?? ""
        baseDeductionField.stringValue = baseDeductionAmount.map { money($0) } ?? ""
        baseDeductionField.isHidden = !useBaseDeduction
        useBaseDeductionButton.state = useBaseDeduction ? .on : .off
        initial = state.initial
        history = state.history
        costAdditions = state.costAdditions ?? []
        settlement = state.settlement ?? .default
    }

    private func loadPoolState() {
        guard let data = UserDefaults.standard.data(forKey: "PoolAnalyzerState"),
              let state = try? JSONDecoder().decode(PoolAnalyzerState.self, from: data) else {
            return
        }
        poolHistory = state.history
        selectedPoolGroups = state.selectedGroups ?? ["PLUS共享号池", "K12共享号池"]
        hasLocalPoolSelection = state.selectedGroups != nil
        availablePoolGroups = state.availableGroups ?? ["PLUS共享号池", "K12共享号池"]
        poolPollingMinutes = normalizedPollingMinutes(state.pollingMinutes ?? defaultPollingMinutes)
        poolPollingField.stringValue = money(poolPollingMinutes)
        smtpSettings = loadSMTPSettings() ?? SMTPSettings(
            host: SMTPSettings.default.host,
            port: SMTPSettings.default.port,
            username: "",
            password: "",
            recipient: state.warningEmail ?? SMTPSettings.default.recipient
        )
        updatePoolGroupsLabel()
        poolAccessToken = state.accessToken
        poolRefreshToken = state.refreshToken
    }

    private func parsePoolGroups(_ text: String) -> [String] {
        text
            .replacingOccurrences(of: "，", with: ",")
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private func normalizedEmail(_ text: String) -> String {
        var value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.contains("@"), !value.isEmpty {
            value += "@qq.com"
        }
        return value
    }

    private func parseBalanceAccounts(_ text: String) -> [BalanceAccount] {
        if let jsonAccounts = parseBalanceAccountsJSON(text) {
            return normalizeBalanceAccounts(jsonAccounts)
        }
        return text
            .split(whereSeparator: \.isNewline)
            .compactMap { rawLine -> BalanceAccount? in
                let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !line.isEmpty else { return nil }
                let parts: [String]
                if line.contains("---") {
                    parts = line
                        .components(separatedBy: "---")
                        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                } else if line.contains(",") || line.contains("，") {
                    parts = line
                        .replacingOccurrences(of: "，", with: ",")
                        .split(separator: ",", omittingEmptySubsequences: false)
                        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                } else {
                    parts = line
                        .split(whereSeparator: { $0 == " " || $0 == "\t" })
                        .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
                }
                guard parts.count >= 2 else { return nil }
                let name = parts[0]
                let apiKey = parts.count >= 3 ? parts[2] : parts[1]
                guard !name.isEmpty, !apiKey.isEmpty, !apiKey.contains("...") else { return nil }
                return BalanceAccount(name: name, baseURL: defaultBalanceBaseURL, apiKey: apiKey)
            }
    }

    private func parseBalanceAccountsJSON(_ text: String) -> [BalanceAccount]? {
        guard let data = text.data(using: .utf8),
              let raw = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            return nil
        }
        let accounts = raw.compactMap { item -> BalanceAccount? in
            let name = (item["name"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let apiKey = ((item["api_key"] as? String) ?? (item["apiKey"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty, !apiKey.isEmpty, !apiKey.contains("...") else { return nil }
            return BalanceAccount(name: name, baseURL: defaultBalanceBaseURL, apiKey: apiKey)
        }
        return accounts.isEmpty ? nil : accounts
    }

    private func normalizeBalanceAccounts(_ accounts: [BalanceAccount]) -> [BalanceAccount] {
        accounts.map { BalanceAccount(name: $0.name, baseURL: defaultBalanceBaseURL, apiKey: $0.apiKey) }
    }

    private func formatBalanceAccountsForEditing(_ accounts: [BalanceAccount]) -> String {
        normalizeBalanceAccounts(accounts).map { "\($0.name)---\($0.apiKey)" }.joined(separator: "\n")
    }

    private func keychainQuery() -> [String: Any] {
        keychainQuery(account: "quota-dashboard")
    }

    private func keychainQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "local.gpt.cost.calculator.pool-api",
            kSecAttrAccount as String: account
        ]
    }

    private func savePoolCredentials(_ credentials: PoolCredentials) -> Bool {
        guard let data = try? JSONEncoder().encode(credentials) else { return false }
        var query = keychainQuery()
        SecItemDelete(query as CFDictionary)
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    private func loadPoolCredentials() -> PoolCredentials? {
        var query = keychainQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else {
            return nil
        }
        return try? JSONDecoder().decode(PoolCredentials.self, from: data)
    }

    private func saveSMTPSettings(_ settings: SMTPSettings) -> Bool {
        guard let data = try? JSONEncoder().encode(settings) else { return false }
        var query = keychainQuery(account: "pool-warning-smtp")
        SecItemDelete(query as CFDictionary)
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    private func loadSMTPSettings() -> SMTPSettings? {
        var query = keychainQuery(account: "pool-warning-smtp")
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else {
            return nil
        }
        return try? JSONDecoder().decode(SMTPSettings.self, from: data)
    }

    private func saveBalanceAccounts(_ accounts: [BalanceAccount]) -> Bool {
        guard let data = try? JSONEncoder().encode(normalizeBalanceAccounts(accounts)) else { return false }
        var query = keychainQuery(account: "balance-accounts")
        SecItemDelete(query as CFDictionary)
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    private func loadBalanceAccounts() -> [BalanceAccount] {
        var query = keychainQuery(account: "balance-accounts")
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let accounts = try? JSONDecoder().decode([BalanceAccount].self, from: data) else {
            return []
        }
        let normalized = normalizeBalanceAccounts(accounts)
        if normalized.map(\.baseURL) != accounts.map(\.baseURL) {
            _ = saveBalanceAccounts(normalized)
        }
        return normalized
    }
}

struct AppError: LocalizedError {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? {
        message
    }
}

private extension KeyedDecodingContainer {
    func decodeFlexibleInt(forKey key: Key) throws -> Int {
        try decodeFlexibleOptionalInt(forKey: key) ?? 0
    }

    func decodeFlexibleOptionalInt(forKey key: Key) throws -> Int? {
        if let value = try decodeIfPresent(Int.self, forKey: key) {
            return value
        }
        if let value = try decodeIfPresent(Double.self, forKey: key) {
            return Int(value.rounded())
        }
        if let value = try decodeIfPresent(String.self, forKey: key) {
            return Int(Double(value) ?? 0)
        }
        return nil
    }

    func decodeFlexibleOptionalDouble(forKey key: Key) throws -> Double? {
        if let value = try decodeIfPresent(Double.self, forKey: key) {
            return value
        }
        if let value = try decodeIfPresent(Int.self, forKey: key) {
            return Double(value)
        }
        if let value = try decodeIfPresent(String.self, forKey: key) {
            return Double(value)
        }
        return nil
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
