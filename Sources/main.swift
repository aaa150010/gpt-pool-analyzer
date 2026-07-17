import Cocoa
import Sparkle
import Vision

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
    var initial: Snapshot?
    var history: [Snapshot]
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
    var concurrentAvailable: Int
    var concurrentTotal: Int
    var limited: Int
    var quotaProtected: Int
    var error: Int
    var disabled: Int
}

struct PoolAnalyzerState: Codable {
    var history: [PoolSnapshot]
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate {
    private let updaterController = SPUStandardUpdaterController(
        startingUpdater: true,
        updaterDelegate: nil,
        userDriverDelegate: nil
    )
    private var window: NSWindow!
    private let costField = NSTextField(string: "200")
    private let addCostField = NSTextField(string: "")
    private let statusLabel = NSTextField(labelWithString: "")
    private let fixedFeePercent = 15.0
    private var pasteMonitor: Any?
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
    private let baseValue = NSTextField(labelWithString: "--")
    private let currentValue = NSTextField(labelWithString: "--")
    private let netValue = NSTextField(labelWithString: "--")
    private let resultValue = NSTextField(labelWithString: "--")
    private let progressValue = NSTextField(labelWithString: "--")
    private let remainingValue = NSTextField(labelWithString: "--")
    private let comparisonTable = NSTableView()
    private let historyTable = NSTableView()
    private let plusPoolHistoryTable = NSTableView()
    private let k12PoolHistoryTable = NSTableView()
    private let tabControl = NSSegmentedControl(labels: ["账号池分析", "成本计算", "成本历史"], trackingMode: .selectOne, target: nil, action: nil)
    private let contentHost = NSView()
    private var topPanel: NSView?
    private var metricWrap: NSView?
    private var activeContentView: NSView?
    private var poolSummaryLabels: [String: (total: NSTextField, schedulable: NSTextField, status: NSTextField, change: NSTextField, time: NSTextField)] = [:]

    private var initial: Snapshot?
    private var history: [Snapshot] = []
    private var poolHistory: [PoolSnapshot] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        loadState()
        loadPoolState()
        refreshOutput()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let pasteMonitor {
            NSEvent.removeMonitor(pasteMonitor)
        }
        feedbackHideTimer?.invalidate()
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
        configureField(addCostField, placeholder: "0")
        addCostField.target = self
        addCostField.action = #selector(addCost)

        let initialButton = button("粘贴为基准截图", action: #selector(importInitial))
        let latestButton = button("粘贴为最新截图", action: #selector(importLatest))
        let resetButton = button("一键重置", action: #selector(resetAll))
        let addCostButton = button("累加成本", action: #selector(addCost))
        let buttonRow = NSStackView(views: [initialButton, latestButton, resetButton])
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

        let costTopPanel = buildTopPanel(buttonRow: buttonRow, addCostButton: addCostButton)
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

        pasteMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.modifierFlags.intersection(.deviceIndependentFlagsMask).contains(.command),
               event.charactersIgnoringModifiers?.lowercased() == "v" {
                self?.pasteSmart()
                return nil
            }
            return event
        }
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

    private func buildTopPanel(buttonRow: NSStackView, addCostButton: NSButton) -> NSView {
        let panel = NSView()
        panel.translatesAutoresizingMaskIntoConstraints = false

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 6
        stack.alignment = .leading
        stack.edgeInsets = NSEdgeInsets(top: 12, left: 20, bottom: 8, right: 20)
        stack.translatesAutoresizingMaskIntoConstraints = false

        let inputRow = NSStackView()
        inputRow.orientation = .horizontal
        inputRow.spacing = 8
        inputRow.alignment = .centerY
        inputRow.addArrangedSubview(label("成本"))
        inputRow.addArrangedSubview(costField)
        inputRow.addArrangedSubview(label("追加成本"))
        inputRow.addArrangedSubview(addCostField)
        inputRow.addArrangedSubview(addCostButton)
        for button in Array(buttonRow.arrangedSubviews) {
            buttonRow.removeArrangedSubview(button)
            inputRow.addArrangedSubview(button)
        }

        stack.addArrangedSubview(inputRow)

        panel.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
            stack.trailingAnchor.constraint(lessThanOrEqualTo: panel.trailingAnchor),
            stack.topAnchor.constraint(equalTo: panel.topAnchor),
            stack.bottomAnchor.constraint(equalTo: panel.bottomAnchor),
            panel.heightAnchor.constraint(equalToConstant: 54)
        ])
        return panel
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
        tabControl.setWidth(112, forSegment: 0)
        tabControl.setWidth(92, forSegment: 1)
        tabControl.setWidth(92, forSegment: 2)
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
        statusLabel.maximumNumberOfLines = 1
        statusLabel.lineBreakMode = .byTruncatingTail
        statusLabel.translatesAutoresizingMaskIntoConstraints = false

        bar.addSubview(statusLabel)
        NSLayoutConstraint.activate([
            bar.heightAnchor.constraint(equalToConstant: 36),
            statusLabel.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 18),
            statusLabel.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -18),
            statusLabel.centerYAnchor.constraint(equalTo: bar.centerYAnchor)
        ])
        return bar
    }

    private func showFeedback(_ message: String, color: NSColor = .white, autoHide: Bool = true) {
        feedbackHideTimer?.invalidate()
        statusLabel.stringValue = message
        statusLabel.textColor = color
        feedbackBar?.isHidden = false
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
        let isPoolTab = tabControl.selectedSegment == 0
        topPanel?.isHidden = isPoolTab
        metricWrap?.isHidden = isPoolTab
        let nextView: NSView
        switch tabControl.selectedSegment {
        case 1:
            nextView = buildCalcPanel()
        case 2:
            nextView = buildHistoryPanel()
        default:
            nextView = buildPoolPanel()
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
    }

    private func buildPoolPanel() -> NSView {
        let panel = NSView()
        panel.wantsLayer = true
        panel.layer?.backgroundColor = NSColor.white.cgColor

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 14
        stack.alignment = .width
        stack.translatesAutoresizingMaskIntoConstraints = false

        let summaryRow = NSStackView(views: [
            poolSummaryCard(groupName: "PLUS共享号池", accent: .systemTeal),
            poolSummaryCard(groupName: "K12共享号池", accent: .systemOrange)
        ])
        summaryRow.orientation = .horizontal
        summaryRow.spacing = 12
        summaryRow.distribution = .fillEqually
        summaryRow.translatesAutoresizingMaskIntoConstraints = false

        let plusSection = poolHistorySection(title: "PLUS共享号池历史", groupName: "PLUS共享号池", table: plusPoolHistoryTable)
        let k12Section = poolHistorySection(title: "K12共享号池历史", groupName: "K12共享号池", table: k12PoolHistoryTable)
        plusSection.setContentHuggingPriority(.defaultLow, for: .vertical)
        k12Section.setContentHuggingPriority(.defaultLow, for: .vertical)
        plusSection.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        k12Section.setContentCompressionResistancePriority(.defaultLow, for: .vertical)

        stack.addArrangedSubview(summaryRow)
        stack.addArrangedSubview(plusSection)
        stack.addArrangedSubview(k12Section)
        NSLayoutConstraint.activate([
            summaryRow.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            summaryRow.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            plusSection.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            plusSection.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            k12Section.leadingAnchor.constraint(equalTo: stack.leadingAnchor),
            k12Section.trailingAnchor.constraint(equalTo: stack.trailingAnchor),
            plusSection.heightAnchor.constraint(equalTo: k12Section.heightAnchor)
        ])

        panel.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: panel.topAnchor, constant: 14),
            stack.bottomAnchor.constraint(equalTo: panel.bottomAnchor, constant: -16)
        ])
        updatePoolSummaryLabels()
        return panel
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
                ("总账号", "total", 92),
                ("5h可调度剩余", "remaining5h", 122),
                ("7d可调度剩余", "remaining7d", 122),
                ("并发可用", "concurrent", 150),
                ("限流", "limited", 98),
                ("额度保护", "quotaProtected", 112),
                ("错误", "error", 96),
                ("禁用", "disabled", 78),
                ("较上次变化", "delta", 82)
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
                ("序号", "index", 80),
                ("账号", "account", 250),
                ("基准余额", "base", 135),
                ("当前余额", "current", 135),
                ("变化金额", "delta", 145),
                ("新增百分比", "growth", 145)
            ]
        )
        comparisonScroll.setContentHuggingPriority(.defaultLow, for: .horizontal)
        comparisonScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 360).isActive = true

        stack.addArrangedSubview(comparisonScroll)
        NSLayoutConstraint.activate([
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

    private func buildHistoryPanel() -> NSView {
        let panel = NSView()
        panel.wantsLayer = true
        panel.layer?.backgroundColor = NSColor.white.cgColor

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.spacing = 8
        stack.alignment = .width
        stack.translatesAutoresizingMaskIntoConstraints = false

        let scroll = makeTableScroll(
            table: historyTable,
            columns: [
                ("序号", "index", 90),
                ("时间", "time", 170),
                ("余额合计", "total", 190),
                ("较基准", "gross", 190),
                ("扣成本后收益", "afterCost", 210)
            ]
        )
        scroll.setContentHuggingPriority(.defaultLow, for: .horizontal)
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 390).isActive = true

        stack.addArrangedSubview(scroll)
        NSLayoutConstraint.activate([
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
            metricCard(title: "基准余额合计", value: baseValue, color: .systemPurple),
            metricCard(title: "现在余额合计", value: currentValue, color: .systemTeal),
            metricCard(title: "扣成本后结果", value: resultValue, color: .systemOrange),
            metricCard(title: "回本进度", value: progressValue, color: .systemPink),
            metricCard(title: "回本状态", value: remainingValue, color: .systemRed),
            metricCard(title: "当前收益", value: netValue, color: .systemGreen)
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
        value.cell?.isScrollable = true

        let valueScroll = NSScrollView()
        valueScroll.drawsBackground = false
        valueScroll.borderType = .noBorder
        valueScroll.hasHorizontalScroller = true
        valueScroll.hasVerticalScroller = false
        valueScroll.autohidesScrollers = true
        valueScroll.documentView = value
        valueScroll.heightAnchor.constraint(equalToConstant: 25).isActive = true
        valueScroll.widthAnchor.constraint(greaterThanOrEqualToConstant: 92).isActive = true

        stack.addArrangedSubview(titleLabel)
        stack.addArrangedSubview(valueScroll)
        card.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: card.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: card.trailingAnchor),
            stack.topAnchor.constraint(equalTo: card.topAnchor),
            stack.bottomAnchor.constraint(equalTo: card.bottomAnchor),
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
        saveState()
        refreshOutput()
    }

    @objc private func addCost() {
        let increment = Double(addCostField.stringValue.replacingOccurrences(of: ",", with: "")) ?? 0
        guard increment != 0 else {
            showFeedback("请输入要累加的成本金额。", color: .systemOrange)
            return
        }
        let newCost = cost + increment
        costField.stringValue = money(newCost)
        addCostField.stringValue = ""
        showFeedback("已累加成本：\(signedMoney(increment)) 元，当前成本 \(money(newCost)) 元。", color: .systemGreen)
        saveState()
        refreshOutput()
    }

    @objc private func importInitial() {
        pasteFromClipboard(asInitial: true)
    }

    @objc private func importLatest() {
        guard initial != nil else {
            showError("请先粘贴基准截图。")
            return
        }
        pasteFromClipboard(asInitial: false)
    }

    @objc private func resetAll() {
        initial = nil
        history = []
        UserDefaults.standard.removeObject(forKey: "StoredState")
        showFeedback("成本计算数据已重置。", color: .systemGreen)
        refreshOutput()
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
            showFeedback("已识别最新截图：\(ocr.accounts.count) 个账号，\(ocr.amounts.count) 个余额。", color: .systemGreen)
        }
        if asInitial ?? false {
            showFeedback("已识别基准截图：\(ocr.accounts.count) 个账号，\(ocr.amounts.count) 个余额。", color: .systemGreen)
        }
        finishPaste(success: true)
        saveState()
        refreshOutput()
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

    private var cost: Double {
        max(Double(costField.stringValue.replacingOccurrences(of: ",", with: "")) ?? 0, 0)
    }

    private func refreshOutput() {
        updateMetrics()
        updatePoolSummaryLabels()
        comparisonTable.reloadData()
        historyTable.reloadData()
        plusPoolHistoryTable.reloadData()
        k12PoolHistoryTable.reloadData()
        scrollToLatestRows()
    }

    private func scrollToLatestRows() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            for table in [self.historyTable, self.plusPoolHistoryTable, self.k12PoolHistoryTable] {
                let lastRow = table.numberOfRows - 1
                if lastRow >= 0 {
                    table.scrollRowToVisible(lastRow)
                }
            }
        }
    }

    private func appendPoolSnapshot(_ snapshot: PoolSnapshot) {
        poolHistory.append(snapshot)
        savePoolState()
        finishPaste(success: true)
        showFeedback("已识别 \(snapshot.groupName)：总账号 \(snapshot.total)，并发可用 \(snapshot.concurrentAvailable)/\(snapshot.concurrentTotal)。", color: .systemGreen)
        if tabControl.selectedSegment != 0 {
            tabControl.selectedSegment = 0
            switchTab(nil)
        } else {
            refreshOutput()
        }
    }

    private func poolRows(for tableView: NSTableView) -> [PoolSnapshot] {
        let groupFilter = tableView == k12PoolHistoryTable ? "K12共享号池" : "PLUS共享号池"
        return poolHistory
            .filter { $0.groupName == groupFilter }
            .sorted { $0.date < $1.date }
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
            labels.time.stringValue = "最新截图：\(formatDate(latest.date))"
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
        guard let initial else {
            setMetric("base", field: baseValue, value: nil, suffix: " 元")
            setMetric("current", field: currentValue, value: nil, suffix: " 元")
            setMetric("net", field: netValue, value: nil, suffix: " 元")
            setMetric("result", field: resultValue, value: nil, suffix: " 元")
            setMetric("progress", field: progressValue, value: nil, suffix: "%")
            setMetricText("remaining", field: remainingValue, text: "--")
            return
        }

        setMetric("base", field: baseValue, value: initial.total, suffix: " 元")
        guard let latest = history.last else {
            setMetric("current", field: currentValue, value: nil, suffix: " 元")
            setMetric("net", field: netValue, value: nil, suffix: " 元")
            setMetric("result", field: resultValue, value: nil, suffix: " 元")
            setMetric("progress", field: progressValue, value: nil, suffix: "%")
            setMetricText("remaining", field: remainingValue, text: "--")
            return
        }

        let gross = latest.total - initial.total
        let netBeforeCost = gross * ((100 - fixedFeePercent) / 100)
        let currentProfit = netBeforeCost - cost
        let progress = cost > 0 ? netBeforeCost / cost * 100 : 0
        let paybackDelta = currentProfit

        setMetric("current", field: currentValue, value: latest.total, suffix: " 元")
        setMetric("net", field: netValue, value: currentProfit, suffix: " 元")
        netValue.textColor = currentProfit >= 0 ? .systemGreen : .systemRed
        setMetric("result", field: resultValue, value: currentProfit, suffix: " 元")
        resultValue.textColor = currentProfit >= 0 ? .systemGreen : .systemRed
        setMetric("progress", field: progressValue, value: progress, suffix: "%", decimals: 1)
        setMetricText("remaining", field: remainingValue, text: "\(signedMoney(paybackDelta)) 元")
        remainingValue.textColor = paybackDelta >= 0 ? .systemGreen : .systemRed
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

        let startValue = displayedMetricValues[key] ?? value
        displayedMetricValues[key] = value

        guard startValue != value else {
            field.stringValue = formatMetric(value, suffix: suffix, decimals: decimals)
            resizeMetricField(field)
            return
        }

        let steps = 22
        let interval = 0.018
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
            field.stringValue = self.formatMetric(current, suffix: suffix, decimals: decimals)
            self.resizeMetricField(field)
            if step >= steps {
                field.stringValue = self.formatMetric(value, suffix: suffix, decimals: decimals)
                self.resizeMetricField(field)
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
        if tableView == plusPoolHistoryTable || tableView == k12PoolHistoryTable {
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

        if tableView == plusPoolHistoryTable || tableView == k12PoolHistoryTable {
            let rows = poolRows(for: tableView)
            guard row < rows.count else { return container }
            let item = rows[row]
            let previous = previousPoolSnapshot(before: item)
            switch identifier {
            case "time":
                cell.stringValue = formatDate(item.date)
            case "total":
                setTrendCell(cell, current: item.total, previous: previous?.total)
            case "remaining5h":
                if let value = item.remaining5h {
                    setTrendCell(cell, current: value, previous: previous?.remaining5h)
                } else {
                    cell.stringValue = "--"
                    cell.textColor = .secondaryLabelColor
                }
            case "remaining7d":
                if let value = item.remaining7d {
                    setTrendCell(cell, current: value, previous: previous?.remaining7d)
                } else {
                    cell.stringValue = "--"
                    cell.textColor = .secondaryLabelColor
                }
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
            let start = row < initial.amounts.count ? initial.amounts[row] : 0
            let end = row < latest.amounts.count ? latest.amounts[row] : 0
            let delta = end - start
            let accounts = initial.accounts ?? latest.accounts ?? []
            let account = row < accounts.count ? accounts[row] : "账号 \(row + 1)"
            switch identifier {
            case "index":
                cell.stringValue = "\(row + 1)"
            case "account":
                cell.stringValue = account
            case "base":
                cell.stringValue = money(start)
            case "current":
                cell.stringValue = money(end)
            case "delta":
                cell.stringValue = signedMoney(delta)
                cell.textColor = delta >= 0 ? .systemGreen : .systemRed
            case "growth":
                if start == 0 {
                    cell.stringValue = "--"
                } else {
                    let growth = delta / start * 100
                    cell.stringValue = signedPercent(growth)
                    cell.textColor = growth >= 0 ? .systemGreen : .systemRed
                }
            case "intervalCompare":
                if let value = intervalComparePercent(row: row) {
                    cell.stringValue = signedPercent(value)
                    cell.textColor = value >= 0 ? .systemGreen : .systemRed
                } else {
                    cell.stringValue = "--"
                }
            default:
                break
            }
            return container
        }

        if tableView == historyTable {
            guard let initial else { return container }
            let item = history[row]
            let gross = item.total - initial.total
            let net = gross * ((100 - fixedFeePercent) / 100)
            let afterCost = net - cost
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
        lines.append("成本合计：\(money(cost)) 元    手续费：固定 15%    到手系数：85.0%")
        lines.append("")

        guard let initial else {
            lines.append("请先复制基准截图并按 Command+V。")
            return lines.joined(separator: "\n")
        }

        lines.append("基准截图")
        lines.append("  时间：\(formatDate(initial.date))")
        lines.append("  基准余额合计：\(amountList(initial.amounts)) = \(money(initial.total)) 元")
        lines.append("")

        guard let latest = history.last else {
            lines.append("请继续复制最新截图并按 Command+V。")
            return lines.joined(separator: "\n")
        }

        let feeFactor = (100 - fixedFeePercent) / 100
        let gross = latest.total - initial.total
        let net = gross * feeFactor
        let currentProfitAfterCost = net - cost
        let remaining = net - cost
        let progress = cost > 0 ? net / cost * 100 : 0
        lines.append("最新截图")
        lines.append("  时间：\(formatDate(latest.date))")
        lines.append("  现在余额合计：\(amountList(latest.amounts)) = \(money(latest.total)) 元")
        lines.append("")
        lines.append("各号余额对比")
        lines.append(balanceTable(initial: initial.amounts, latest: latest.amounts))
        lines.append("")
        lines.append("当前结果")
        lines.append("  扣成本后当前结果：\(money(currentProfitAfterCost)) 元")
        lines.append("")
        lines.append("回本状态")
        lines.append("  回本进度：\(percent(progress))")
        lines.append("  距离回本还差：\(money(remaining)) 元")

        return lines.joined(separator: "\n")
    }

    private func buildHistoryReport() -> String {
        guard let initial else {
            return "请先复制基准截图并按 Command+V。"
        }
        guard !history.isEmpty else {
            return "暂无历史记录。复制最新截图后按 Command+V，会自动追加到这里。"
        }

        let feeFactor = (100 - fixedFeePercent) / 100
        var rows = [
            "┌──────┬──────────┬──────────────┬──────────────┬──────────────┬──────────────┐",
            "│ 序号 │ 时间     │ 余额合计(元) │ 较基准(元)   │ 扣成本结果   │",
            "├──────┼──────────┼──────────────┼──────────────┼──────────────┤"
        ]

        for (index, item) in history.enumerated() {
            let gross = item.total - initial.total
            let net = gross * feeFactor
            let afterCost = net - cost
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

    private func balanceTable(initial: [Double], latest: [Double]) -> String {
        var rows = [
            "  ┌──────┬──────────────┬──────────────┬──────────────┐",
            "  │ 账号 │ 基准余额(元) │ 当前余额(元) │ 变化金额(元) │",
            "  ├──────┼──────────────┼──────────────┼──────────────┤"
        ]
        let count = max(initial.count, latest.count)
        for index in 0..<count {
            let start = index < initial.count ? initial[index] : 0
            let end = index < latest.count ? latest[index] : 0
            let delta = end - start
            rows.append(String(format: "  │ %4d │ %12.2f │ %12.2f │ %+12.2f │", index + 1, start, end, delta))
        }
        rows.append("  └──────┴──────────────┴──────────────┴──────────────┘")
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
        showFeedback(message, color: .systemRed)
        let alert = NSAlert()
        alert.messageText = "处理失败"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }

    private func saveState() {
        let state = StoredState(cost: cost, initial: initial, history: history)
        if let data = try? JSONEncoder().encode(state) {
            UserDefaults.standard.set(data, forKey: "StoredState")
        }
    }

    private func savePoolState() {
        let state = PoolAnalyzerState(history: poolHistory)
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
        initial = state.initial
        history = state.history
    }

    private func loadPoolState() {
        guard let data = UserDefaults.standard.data(forKey: "PoolAnalyzerState"),
              let state = try? JSONDecoder().decode(PoolAnalyzerState.self, from: data) else {
            return
        }
        poolHistory = state.history
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

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
