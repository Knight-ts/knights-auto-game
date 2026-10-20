# 好娃提醒 - 原生窗口版（WinForms，不依赖浏览器）
# 数据通过本机控制台接口读写（默认 http://127.0.0.1:8790）

param([string]$BaseUrl = "http://127.0.0.1:8790")

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# 高分屏下避免窗口被系统整体放大后裁切
try {
    Add-Type -Namespace Win32 -Name Dpi -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
'@
    [void][Win32.Dpi]::SetProcessDPIAware()
} catch { }

$script:conditions = @()
$script:groups = @()
$script:featureView = @()
$script:featurePicked = @{}
$script:index = -1

$attrs = @("力量", "体质", "技巧", "感知", "敏捷", "意志")
$grades = @("", "E", "D", "C", "B", "A", "S", "SS")

# ---------------------------------------------------------------- 接口

function Get-State {
    $state = Invoke-RestMethod -Uri "$BaseUrl/api/state" -TimeoutSec 10
    if (-not ($state.PSObject.Properties.Name -contains 'conditions')) {
        throw "这个地址不是网页控制台（/api/state 里没有 conditions）：$BaseUrl。请确认 Knights Auto Game 的控制台在运行，端口没被网页版占用。"
    }
    $script:conditions = @(@($state.conditions) | Where-Object { $null -ne $_ })
    $script:groups = @(@($state.featureGroups) | Where-Object { $null -ne $_ })
    return $state
}

function Post-Json([string]$path, $payload) {
    $json = $payload | ConvertTo-Json -Depth 12 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Uri "$BaseUrl$path" -Method Post -Body $bytes `
        -ContentType "application/json; charset=utf-8" -TimeoutSec 30
}

# ---------------------------------------------------------------- 界面

$form = New-Object System.Windows.Forms.Form
$form.Text = "好娃提醒 - Knights Auto Game"
$form.Size = New-Object System.Drawing.Size(1120, 830)
$form.StartPosition = "Manual"
$form.Location = New-Object System.Drawing.Point(60, 40)
$form.MinimumSize = New-Object System.Drawing.Size(1000, 700)
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::None
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei", 9)

$listHeader = New-Object System.Windows.Forms.Label
$listHeader.Text = "组合（每行一个，选中后在右侧填写详情）"
$listHeader.Location = New-Object System.Drawing.Point(12, 10)
$listHeader.AutoSize = $true
$listHeader.ForeColor = [System.Drawing.Color]::DimGray

$detailHeader = New-Object System.Windows.Forms.Label
$detailHeader.Text = "详情"
$detailHeader.Location = New-Object System.Drawing.Point(270, 9)
$detailHeader.AutoSize = $true
$detailHeader.Font = New-Object System.Drawing.Font("Microsoft YaHei", 11, [System.Drawing.FontStyle]::Bold)

$list = New-Object System.Windows.Forms.ListBox
$list.Location = New-Object System.Drawing.Point(12, 34)
$list.Size = New-Object System.Drawing.Size(246, 638)
$list.Font = New-Object System.Drawing.Font("Microsoft YaHei", 10)
$list.IntegralHeight = $false

$panel = New-Object System.Windows.Forms.Panel
$panel.Location = New-Object System.Drawing.Point(270, 34)
$panel.Size = New-Object System.Drawing.Size(880, 638)
$panel.AutoScroll = $true

function New-Label([string]$text, [int]$x, [int]$y, [int]$w = 96) {
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = $text
    $lbl.AutoSize = $true
    $lbl.Location = New-Object System.Drawing.Point($x, $y)
    $lbl.Size = New-Object System.Drawing.Size($w, 22)
    $lbl.ForeColor = [System.Drawing.Color]::DimGray
    return $lbl
}

function New-TextBox([int]$x, [int]$y, [int]$w) {
    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Location = New-Object System.Drawing.Point($x, $y)
    $tb.Size = New-Object System.Drawing.Size($w, 24)
    return $tb
}

$panel.Controls.Add((New-Label "显示名" 0 8 60))
$caseName = New-TextBox 64 6 240
$panel.Controls.Add($caseName)
$panel.Controls.Add((New-Label "内部 ID" 314 8 60))
$idBox = New-TextBox 378 6 170
$panel.Controls.Add($idBox)
$panel.Controls.Add((New-Label "节点" 558 8 40))
$nodeBox = New-TextBox 602 6 270
$panel.Controls.Add($nodeBox)

$panel.Controls.Add((New-Label "条件名" 0 42 60))
$nameBox = New-TextBox 64 40 420
$panel.Controls.Add($nameBox)

$panel.Controls.Add((New-Label "说明" 0 76 60))
$descBox = New-TextBox 64 74 808
$panel.Controls.Add($descBox)

$defaultBox = New-Object System.Windows.Forms.CheckBox
$defaultBox.Text = "默认勾选"
$defaultBox.Location = New-Object System.Drawing.Point(0, 108)
$defaultBox.Size = New-Object System.Drawing.Size(120, 24)
$panel.Controls.Add($defaultBox)

$panel.Controls.Add((New-Label "必需特性（一行一个）" 0 138 220))
$featuresBox = New-Object System.Windows.Forms.TextBox
$featuresBox.Location = New-Object System.Drawing.Point(0, 162)
$featuresBox.Size = New-Object System.Drawing.Size(300, 170)
$featuresBox.Multiline = $true
$featuresBox.ScrollBars = "Vertical"
$panel.Controls.Add($featuresBox)

$panel.Controls.Add((New-Label "属性门槛（等级为 >=；最小值可留空）" 316 138 560))
$y = 164
$gradeBoxes = @{}
$minBoxes = @{}
foreach ($attr in $attrs) {
    $panel.Controls.Add((New-Label $attr 316 $y 56))
    $cb = New-Object System.Windows.Forms.ComboBox
    $cb.Location = New-Object System.Drawing.Point(372, ($y - 3))
    $cb.Size = New-Object System.Drawing.Size(70, 24)
    $cb.DropDownStyle = "DropDownList"
    [void]$cb.Items.AddRange($grades)
    $panel.Controls.Add($cb)
    $gradeBoxes[$attr] = $cb

    $mb = New-TextBox 450 $y 100
    $panel.Controls.Add($mb)
    $minBoxes[$attr] = $mb
    $y += 30
}

$panel.Controls.Add((New-Label "或条件（每行一条，如 敏捷>=0.3；同一行用「且」连接）" 0 350 860))
$orBox = New-Object System.Windows.Forms.TextBox
$orBox.Location = New-Object System.Drawing.Point(0, 374)
$orBox.Size = New-Object System.Drawing.Size(300, 90)
$orBox.Multiline = $true
$orBox.ScrollBars = "Vertical"
$panel.Controls.Add($orBox)

$panel.Controls.Add((New-Label "备注（只给自己看，保存在条件表里）" 0 478 360))
$noteBox = New-Object System.Windows.Forms.TextBox
$noteBox.Location = New-Object System.Drawing.Point(0, 502)
$noteBox.Size = New-Object System.Drawing.Size(808, 86)
$noteBox.Multiline = $true
$noteBox.ScrollBars = "Vertical"
$panel.Controls.Add($noteBox)

$status = New-Object System.Windows.Forms.Label
$status.Location = New-Object System.Drawing.Point(12, 686)
$status.Size = New-Object System.Drawing.Size(1140, 22)
$status.ForeColor = [System.Drawing.Color]::DimGray

function New-Button([string]$text, [int]$x, [int]$w) {
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $text
    $btn.Location = New-Object System.Drawing.Point($x, 718)
    $btn.Size = New-Object System.Drawing.Size($w, 32)
    return $btn
}

$btnAdd = New-Button "新增组合" 12 90
$btnDel = New-Button "删除组合" 110 90
$btnReload = New-Button "重新读取" 208 90
$btnFeature = New-Button "特性表…" 306 90
$btnFeature.ForeColor = [System.Drawing.Color]::FromArgb(79, 140, 255)
$btnSync = New-Button "同步到 Knights Auto Game" 404 150
$btnSave = New-Button "保存并同步" 562 130
$btnSave.BackColor = [System.Drawing.Color]::FromArgb(79, 140, 255)
$btnSave.ForeColor = [System.Drawing.Color]::White
$btnClose = New-Button "关闭" 1038 70

$form.Controls.AddRange(@($listHeader, $detailHeader, $list, $panel, $status,
        $btnAdd, $btnDel, $btnReload, $btnFeature, $btnSync, $btnSave, $btnClose))

# ---------------------------------------------------------------- 数据绑定

function Show-Item([int]$i) {
    if ($i -lt 0 -or $i -ge $script:conditions.Count) { return }
    $c = $script:conditions[$i]
    $caseName.Text = [string]$c.caseName
    $idBox.Text = [string]$c.id
    $nameBox.Text = [string]$c.name
    $nodeBox.Text = [string]$c.node
    $descBox.Text = [string]$c.description
    $defaultBox.Checked = [bool]$c.defaultChecked
    $featuresBox.Text = (@($c.features) -join "`r`n")
    $orBox.Text = (@($c.orLines) -join "`r`n")
    $noteBox.Text = [string]$c.note
    foreach ($attr in $attrs) {
        $gradeBoxes[$attr].SelectedItem = [string]$c.grades.$attr
        $minBoxes[$attr].Text = [string]$c.mins.$attr
    }
    $detailHeader.Text = "详情：$($c.caseName)（$(@($c.features).Count) 特性）"
    $status.Text = "编辑：$($c.caseName)　|　$(Format-Summary $c)"
}

function Format-Summary($c) {
    $parts = @()
    foreach ($attr in $attrs) {
        if ($c.grades) {
            $grade = [string]$c.grades.$attr
            if ($grade) { $parts += ($attr + ">=" + $grade) }
        }
        if ($c.mins) {
            $min = [string]$c.mins.$attr
            if ($min) { $parts += ($attr + ">=" + $min) }
        }
    }
    $orLines = @($c.orLines)
    if ($orLines.Count -gt 0) { $parts += ("或 " + ($orLines -join " / ")) }
    if ($parts.Count -eq 0) { $parts = @("不看属性") }
    return ("特性 " + (@($c.features).Count) + " 项 · " + ($parts -join " · "))
}

function Store-Item {
    $i = $script:index
    if ($i -lt 0 -or $i -ge $script:conditions.Count) { return }
    $c = $script:conditions[$i]
    $c.caseName = $caseName.Text.Trim()
    $c.id = $idBox.Text.Trim()
    $c.name = $nameBox.Text.Trim()
    $c.node = $nodeBox.Text.Trim()
    $c.description = $descBox.Text.Trim()
    $c.note = $noteBox.Text.Trim()
    $c.defaultChecked = $defaultBox.Checked
    $c.features = @($featuresBox.Text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $c.orLines = @($orBox.Text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })

    $g = @{}
    $m = @{}
    foreach ($attr in $attrs) {
        $g[$attr] = [string]$gradeBoxes[$attr].SelectedItem
        $m[$attr] = $minBoxes[$attr].Text.Trim()
    }
    $c.grades = $g
    $c.mins = $m
}

function Refresh-List([int]$select = -1) {
    $list.Items.Clear()
    foreach ($c in $script:conditions) {
        [void]$list.Items.Add([string]$c.caseName)
    }
    if ($select -ge 0 -and $select -lt $list.Items.Count) {
        $list.SelectedIndex = $select
    }
}

function Show-FeatureTable([string]$targetName, [string[]]$current) {
    # 完整特性表（按种族分类）：左侧选种族，右侧勾选特性，应用后写回当前组合
    $script:featurePicked = @{}
    foreach ($name in @($current)) {
        if ($name) { $script:featurePicked[[string]$name] = $true }
    }
    $script:featureView = @()

    $dialog = New-Object System.Windows.Forms.Form
    $dialog.Text = "完整特性表（按种族分类） - 加入：$targetName"
    $dialog.Size = New-Object System.Drawing.Size(1040, 700)
    $dialog.StartPosition = "CenterParent"
    $dialog.FormBorderStyle = "FixedDialog"
    $dialog.MinimizeBox = $false
    $dialog.MaximizeBox = $false
    $dialog.ShowInTaskbar = $false
    $dialog.Font = $form.Font
    $dialog.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::None

    $hint = New-Object System.Windows.Forms.Label
    $hint.Text = "左键点种族 → 右侧勾选特性 → 点「应用勾选」写回组合（取消勾选＝从组合移除）"
    $hint.Location = New-Object System.Drawing.Point(14, 12)
    $hint.AutoSize = $true
    $hint.ForeColor = [System.Drawing.Color]::DimGray

    $filterBox = New-Object System.Windows.Forms.TextBox
    $filterBox.Location = New-Object System.Drawing.Point(396, 10)
    $filterBox.Size = New-Object System.Drawing.Size(280, 24)
    $filterBox.ForeColor = [System.Drawing.Color]::Gray
    $filterBox.Text = "筛选特性名 / 效果"

    $tree = New-Object System.Windows.Forms.TreeView
    $tree.Location = New-Object System.Drawing.Point(14, 44)
    $tree.Size = New-Object System.Drawing.Size(260, 520)
    $tree.HideSelection = $false

    $listView = New-Object System.Windows.Forms.ListView
    $listView.Location = New-Object System.Drawing.Point(286, 44)
    $listView.Size = New-Object System.Drawing.Size(738, 520)
    $listView.View = "Details"
    $listView.FullRowSelect = $true
    $listView.GridLines = $true
    $listView.CheckBoxes = $true
    $listView.MultiSelect = $true
    [void]$listView.Columns.Add("特性", 150)
    [void]$listView.Columns.Add("等级", 80)
    [void]$listView.Columns.Add("部位", 70)
    [void]$listView.Columns.Add("效果", 380)
    [void]$listView.Columns.Add("寿命", 55)

    $countLabel = New-Object System.Windows.Forms.Label
    $countLabel.Location = New-Object System.Drawing.Point(288, 574)
    $countLabel.AutoSize = $true
    $countLabel.ForeColor = [System.Drawing.Color]::DimGray

    $btnApply = New-Object System.Windows.Forms.Button
    $btnApply.Text = "应用勾选"
    $btnApply.Location = New-Object System.Drawing.Point(288, 604)
    $btnApply.Size = New-Object System.Drawing.Size(150, 34)
    $btnApply.BackColor = [System.Drawing.Color]::FromArgb(79, 140, 255)
    $btnApply.ForeColor = [System.Drawing.Color]::White

    $btnCancel = New-Object System.Windows.Forms.Button
    $btnCancel.Text = "取消"
    $btnCancel.Location = New-Object System.Drawing.Point(448, 604)
    $btnCancel.Size = New-Object System.Drawing.Size(90, 34)
    $dialog.CancelButton = $btnCancel

    $updateCount = {
        $countLabel.Text = "已选 $($script:featurePicked.Count) 项特性"
    }

    $refresh = {
        $listView.BeginUpdate()
        $listView.Items.Clear()
        $keyword = $filterBox.Text.Trim()
        if ($keyword -eq "筛选特性名 / 效果") { $keyword = "" }
        $checkedNow = @{}
        foreach ($key in @($script:featurePicked.Keys)) { $checkedNow[$key] = $true }
        foreach ($feature in @($script:featureView)) {
            $name = [string]$feature.name
            $hay = $name + " " + [string]$feature.level + " " + [string]$feature.position + " " + [string]$feature.effect
            if ($keyword -and ($hay -notlike "*$keyword*")) { continue }
            $item = New-Object System.Windows.Forms.ListViewItem($name)
            [void]$item.SubItems.Add([string]$feature.level)
            [void]$item.SubItems.Add([string]$feature.position)
            [void]$item.SubItems.Add([string]$feature.effect)
            [void]$item.SubItems.Add([string]$feature.lifespan)
            $item.Tag = $name
            [void]$listView.Items.Add($item)
            if ($checkedNow.ContainsKey($name)) { $item.Checked = $true }
        }
        $listView.EndUpdate()
        & $updateCount
    }

    foreach ($group in $script:groups) {
        $kind = [string]$group.kindLabel
        if (-not $kind) { $kind = "通用" }
        $kindNode = $tree.Nodes | Where-Object { $_.Text -eq $kind } | Select-Object -First 1
        if (-not $kindNode) {
            $kindNode = New-Object System.Windows.Forms.TreeNode($kind)
            [void]$tree.Nodes.Add($kindNode)
        }
        $child = New-Object System.Windows.Forms.TreeNode([string]$group.label)
        $child.Tag = $group
        [void]$kindNode.Nodes.Add($child)
    }
    if ($tree.Nodes.Count -gt 0) { $tree.ExpandAll() }

    $tree.Add_AfterSelect({
        param($sender, $eventArgs)
        if ($eventArgs.Node -and $eventArgs.Node.Tag) {
            $script:featureView = @($eventArgs.Node.Tag.features)
        } else {
            $script:featureView = @()
        }
        & $refresh
    })
    $listView.Add_ItemChecked({
        param($sender, $eventArgs)
        $name = [string]$eventArgs.Item.Tag
        if ($eventArgs.Item.Checked) { $script:featurePicked[$name] = $true }
        else { [void]$script:featurePicked.Remove($name) }
        & $updateCount
    })
    $filterBox.Add_GotFocus({
        if ($filterBox.Text -eq "筛选特性名 / 效果") {
            $filterBox.Text = ""
            $filterBox.ForeColor = [System.Drawing.Color]::Black
        }
    })
    $filterBox.Add_LostFocus({
        if (-not $filterBox.Text.Trim()) {
            $filterBox.Text = "筛选特性名 / 效果"
            $filterBox.ForeColor = [System.Drawing.Color]::Gray
            & $refresh
        }
    })
    $filterBox.Add_TextChanged({
        if ($filterBox.Text -ne "筛选特性名 / 效果") { & $refresh }
    })
    $btnApply.Add_Click({ $dialog.DialogResult = [System.Windows.Forms.DialogResult]::OK })
    $btnCancel.Add_Click({ $dialog.DialogResult = [System.Windows.Forms.DialogResult]::Cancel })

    # 默认选中第一个种族，触发 AfterSelect 填充右侧表格
    if ($tree.Nodes.Count -gt 0 -and $tree.Nodes[0].Nodes.Count -gt 0) {
        $tree.SelectedNode = $tree.Nodes[0].Nodes[0]
    } else {
        $script:featureView = @()
        & $refresh
    }

    $dialog.Controls.AddRange(@($hint, $filterBox, $tree, $listView, $countLabel, $btnApply, $btnCancel))

    if ($dialog.ShowDialog($form) -ne [System.Windows.Forms.DialogResult]::OK) { return $null }

    # 输出顺序：先按特性表顺序排，再接上表里没有的自定义特性
    $result = New-Object System.Collections.ArrayList
    $inTable = @{}
    foreach ($group in $script:groups) {
        foreach ($feature in @($group.features)) {
            $name = [string]$feature.name
            if ($inTable.ContainsKey($name)) { continue }
            $inTable[$name] = $true
            if ($script:featurePicked.ContainsKey($name)) { [void]$result.Add($name) }
        }
    }
    foreach ($name in @($current)) {
        $name = [string]$name
        if ($name -and -not $inTable.ContainsKey($name) -and $script:featurePicked.ContainsKey($name)) {
            if (-not $result.Contains($name)) { [void]$result.Add($name) }
        }
    }
    return [pscustomobject]@{ Features = @($result.ToArray()) }
}

function Load-All {
    try {
        [void](Get-State)
        Refresh-List 0
        $script:index = -1
        $script:index = 0
        Show-Item 0
        $status.Text = "已读取 $($script:conditions.Count) 个组合（$($script:groups.Count) 个种族分类）"
    } catch {
        $status.Text = "读取失败：$($_.Exception.Message)。请先启动 Knights Auto Game（Agent 会在后台启动控制台服务）。"
    }
}

$list.Add_SelectedIndexChanged({
    if ($script:index -ge 0) { Store-Item }
    $script:index = $list.SelectedIndex
    Show-Item $script:index
})

$btnAdd.Add_Click({
    Store-Item
    $n = $script:conditions.Count + 1
    $g = @{}
    $m = @{}
    foreach ($attr in $attrs) { $g[$attr] = ""; $m[$attr] = "" }
    $script:conditions += [pscustomobject]@{
        id = "type_$n"; name = "新组合 $n"; description = ""; note = ""
        caseName = "新组合 $n"; node = "检测_新组合$n"
        defaultChecked = $false; features = @(); orLines = @()
        grades = $g; mins = $m
    }
    Refresh-List ($script:conditions.Count - 1)
    $list.SelectedIndex = $script:conditions.Count - 1
})

$btnDel.Add_Click({
    if ($script:index -lt 0) { return }
    if ([System.Windows.Forms.MessageBox]::Show("确定删除这个组合？保存后同时移除检测节点和界面选项。", "删除确认",
            "YesNo", "Warning") -ne "Yes") { return }
    $script:conditions = @($script:conditions | Where-Object { $_ -ne $script:conditions[$script:index] })
    $script:index = -1
    Refresh-List 0
    if ($list.Items.Count -gt 0) { $list.SelectedIndex = 0 }
})

$btnReload.Add_Click({ Load-All })

$btnFeature.Add_Click({
    if ($script:index -lt 0 -or $script:index -ge $script:conditions.Count) {
        $status.Text = "先在左侧选中一个组合，再打开特性表"
        return
    }
    Store-Item
    try {
        $target = $script:conditions[$script:index]
        $result = Show-FeatureTable ([string]$target.caseName) (@($target.features))
        if ($null -ne $result) {
            $picked = @($result.Features)
            $featuresBox.Text = ($picked -join "`r`n")
            $detailHeader.Text = "详情：$($target.caseName)（$($picked.Count) 特性）"
            $status.Text = "已从特性表写回 $($picked.Count) 项特性，记得点「保存并同步」"
        }
    } catch {
        $status.Text = "打开特性表失败：$($_.Exception.Message)"
    }
})

$btnSync.Add_Click({
    try {
        Store-Item
        $names = @($script:conditions | Where-Object { $_.defaultChecked } | ForEach-Object { $_.caseName })
        $r = Post-Json "/api/instance/sync" @{ caseNames = $names }
        $status.Text = $r.message
    } catch {
        $status.Text = "同步失败：$($_.Exception.Message)"
    }
})

$btnSave.Add_Click({
    try {
        Store-Item
        $r = Post-Json "/api/save" @{ conditions = $script:conditions }
        if ($r.ok) {
            $script:conditions = @($r.conditions)
            $keep = $script:index
            Refresh-List $keep
            $status.Text = "保存成功：" + (($r.messages) -join "；")
        } else {
            $status.Text = "保存失败：" + (($r.errors) -join "；")
        }
    } catch {
        $status.Text = "保存失败：$($_.Exception.Message)"
    }
})

$btnClose.Add_Click({ $form.Close() })

Load-All
[void]$form.ShowDialog()
