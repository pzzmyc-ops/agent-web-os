# Long-lived UI Automation server for the computer-use skill.
# Protocol: one JSON request per line on TCP 127.0.0.1, one JSON reply per line.
# Ops: ping | tree | invoke | setvalue | select | toggle | focus | getvalue
# Element indexes are valid only for the latest tree of the same hwnd.
$ErrorActionPreference = 'Stop'
[void][Reflection.Assembly]::LoadWithPartialName('UIAutomationClient')
[void][Reflection.Assembly]::LoadWithPartialName('UIAutomationTypes')
[void][Reflection.Assembly]::LoadWithPartialName('WindowsBase')

$script:cacheHwnd = 0
$script:cache = New-Object System.Collections.ArrayList
$script:UIA = [System.Windows.Automation.AutomationElement]

function Get-Rect($el) {
    try {
        $r = $el.Current.BoundingRectangle
        return @{ x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
    } catch { return @{ x = 0; y = 0; w = 0; h = 0 } }
}

function Get-Patterns($el) {
    $names = New-Object System.Collections.ArrayList
    foreach ($p in @(
        @('Invoke', [System.Windows.Automation.InvokePattern]::Pattern),
        @('Value', [System.Windows.Automation.ValuePattern]::Pattern),
        @('SelectionItem', [System.Windows.Automation.SelectionItemPattern]::Pattern),
        @('Toggle', [System.Windows.Automation.TogglePattern]::Pattern),
        @('ExpandCollapse', [System.Windows.Automation.ExpandCollapsePattern]::Pattern),
        @('ScrollItem', [System.Windows.Automation.ScrollItemPattern]::Pattern)
    )) {
        $obj = $null
        try {
            if ($el.TryGetCurrentPattern($p[1], [ref]$obj)) { [void]$names.Add($p[0]) }
        } catch { }
    }
    return $names
}

function Walk($el, $depth, $max, $list) {
    if ($depth -gt 14 -or $list.Count -ge $max) { return }
    $patterns = Get-Patterns $el
    $role = ''
    $name = ''
    $value = ''
    try { $role = $el.Current.ControlType.ProgrammaticName -replace '^ControlType\.', '' } catch { }
    try { $name = $el.Current.Name } catch { }
    if ($patterns -contains 'Value') {
        try {
            $vp = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
            $value = [string]$vp.Current.Value
        } catch { }
    }
    $hit = ($name -ne '' -or $patterns.Count -gt 0)
    if ($hit) {
        $r = Get-Rect $el
        if ($r.w -gt 0 -and $r.h -gt 0) {
            [void]$list.Add([pscustomobject]@{
                index = $list.Count + 1
                role = $role
                name = $name
                value = $value
                patterns = ($patterns -join ',')
                x = $r.x; y = $r.y; w = $r.w; h = $r.h
                depth = $depth
                _el = $el
            })
        }
    }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $child = $null
    try { $child = $walker.GetFirstChild($el) } catch { return }
    while ($null -ne $child) {
        if ($list.Count -ge $max) { return }
        Walk $child ($depth + 1) $max $list
        try { $child = $walker.GetNextSibling($child) } catch { break }
    }
}

function Handle($req) {
    $op = [string]$req.op
    if ($op -eq 'ping') { return @{ ok = $true; pid = $PID } }
    if ($op -eq 'focus') {
        $el = $script:UIA::FromHandle([IntPtr][long]$req.hwnd)
        $el.SetFocus()
        return @{ ok = $true }
    }
    if ($op -eq 'tree') {
        $hwnd = [long]$req.hwnd
        $el = $script:UIA::FromHandle([IntPtr]$hwnd)
        if ($null -eq $el) { return @{ ok = $false; code = 'no_window' } }
        $max = 400
        if ($req.max) { $max = [int]$req.max }
        $list = New-Object System.Collections.ArrayList
        $sw = [Diagnostics.Stopwatch]::StartNew()
        Walk $el 0 $max $list
        $sw.Stop()
        $script:cacheHwnd = $hwnd
        $script:cache = $list
        $out = New-Object System.Collections.ArrayList
        foreach ($item in $list) {
            $nm = [string]$item.name
            if ($req.query -and ($nm -notlike ('*' + $req.query + '*'))) { continue }
            [void]$out.Add([pscustomobject]@{
                index = $item.index; role = $item.role; name = $item.name; value = $item.value
                patterns = $item.patterns; x = $item.x; y = $item.y; w = $item.w; h = $item.h
            })
        }
        return @{
            ok = $true; hwnd = $hwnd; title = $el.Current.Name; total = $list.Count
            took_ms = [int]$sw.ElapsedMilliseconds; elements = @($out)
        }
    }
    if ($op -eq 'find') {
        # Fast path: let UIA find by name instead of walking the whole tree.
        $hwnd = [long]$req.hwnd
        $root = $script:UIA::FromHandle([IntPtr]$hwnd)
        if ($null -eq $root) { return @{ ok = $false; code = 'no_window' } }
        $want = [string]$req.name
        $cond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::NameProperty, $want)
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $found = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
        $sw.Stop()
        $list = New-Object System.Collections.ArrayList
        foreach ($el in $found) {
            $patterns = Get-Patterns $el
            $r = Get-Rect $el
            if ($r.w -le 0 -or $r.h -le 0) { continue }
            [void]$list.Add([pscustomobject]@{
                index = $list.Count + 1
                role = ($el.Current.ControlType.ProgrammaticName -replace '^ControlType\.', '')
                name = $el.Current.Name
                value = ''
                patterns = ($patterns -join ',')
                x = $r.x; y = $r.y; w = $r.w; h = $r.h
                depth = 0
                _el = $el
            })
        }
        $script:cacheHwnd = $hwnd
        $script:cache = $list
        $out = New-Object System.Collections.ArrayList
        foreach ($item in $list) {
            [void]$out.Add([pscustomobject]@{
                index = $item.index; role = $item.role; name = $item.name; value = $item.value
                patterns = $item.patterns; x = $item.x; y = $item.y; w = $item.w; h = $item.h
            })
        }
        return @{
            ok = $true; hwnd = $hwnd; title = $root.Current.Name; total = $list.Count
            took_ms = [int]$sw.ElapsedMilliseconds; elements = @($out)
        }
    }
    $idx = [int]$req.index
    if ($script:cacheHwnd -ne [long]$req.hwnd -or $idx -lt 1 -or $idx -gt $script:cache.Count) {
        return @{ ok = $false; code = 'stale_index' }
    }
    $target = $script:cache[$idx - 1]
    $el = $target._el
    if ($op -eq 'invoke') {
        $p = $null
        if (-not $el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) {
            return @{ ok = $false; code = 'no_invoke_pattern' }
        }
        $p.Invoke()
        return @{ ok = $true; effect = 'confirmed'; via = 'Invoke' }
    }
    if ($op -eq 'setvalue') {
        $p = $null
        if (-not $el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$p)) {
            return @{ ok = $false; code = 'no_value_pattern' }
        }
        $p.SetValue([string]$req.text)
        $vp = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $read = [string]$vp.Current.Value
        return @{ ok = $true; effect = 'confirmed'; via = 'SetValue'; readback = $read }
    }
    if ($op -eq 'select') {
        $p = $null
        if (-not $el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) {
            return @{ ok = $false; code = 'no_selection_pattern' }
        }
        $p.Select()
        return @{ ok = $true; effect = 'confirmed'; via = 'Select' }
    }
    if ($op -eq 'toggle') {
        $p = $null
        if (-not $el.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$p)) {
            return @{ ok = $false; code = 'no_toggle_pattern' }
        }
        $p.Toggle()
        return @{ ok = $true; effect = 'confirmed'; via = 'Toggle' }
    }
    if ($op -eq 'getvalue') {
        $p = $null
        if (-not $el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$p)) {
            return @{ ok = $false; code = 'no_value_pattern' }
        }
        return @{ ok = $true; value = [string]$p.Current.Value }
    }
    return @{ ok = $false; code = 'unknown_op' }
}

$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$info = Join-Path $env:TEMP 'computer-use-ax.json'
@{ port = $port; pid = $PID; started = [string](Get-Date -Format s) } | ConvertTo-Json -Compress | Set-Content -Path $info -Encoding ascii

while ($true) {
    $client = $listener.AcceptTcpClient()
    try {
        $client.NoDelay = $true
        $stream = $client.GetStream()
        $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::UTF8)
        $writer = New-Object System.IO.StreamWriter($stream, (New-Object Text.UTF8Encoding($false)))
        $writer.AutoFlush = $true
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line.Trim() -eq '') { continue }
            $reply = $null
            try {
                $req = $line | ConvertFrom-Json
                $reply = Handle $req
            } catch {
                $reply = @{ ok = $false; code = 'error'; detail = [string]$_.Exception.Message }
            }
            $writer.WriteLine(($reply | ConvertTo-Json -Compress -Depth 6))
        }
    } catch { } finally {
        try { $client.Close() } catch { }
    }
}
