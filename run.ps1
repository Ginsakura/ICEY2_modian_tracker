<#
.SYNOPSIS
    在后台静默运行配置驱动的摩点多项目采集器，支持启动状态控制
.DESCRIPTION
    以隐藏窗口的独立进程运行 modian_tracker.py：
      - 使用 .venv/Scripts/python.exe（缺失时回退到 PATH 中的 python）
      - 抓取间隔由 trackers.json 的 projects.<name>.interval 决定
      - 抓取日志写入 tracker_log.log；启动器输出写入 logs\ 目录
      - run.pid 由 Python 写入和删除，启动器只轮询等待
      - 支持 start/stop/restart/state 控制
.PARAMETER Action
    控制命令：start（启动）、stop（停止）、restart（重启）、state（查看状态），默认 start
.EXAMPLE
    .\run.ps1
    .\run.ps1 -Action start
    .\run.ps1 -Action stop
    .\run.ps1 -Action restart
    .\run.ps1 -Action state
#>
param(
    [ValidateSet("start", "stop", "restart", "state", "status")]
    [string]$Action = "start"
)

[System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[System.Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$PSDefaultParameterValues['*:Encoding'] = 'utf8'
$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$pidFile = Join-Path $PSScriptRoot "run.pid"
$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "launcher.out.log"
$errLog = Join-Path $logDir "launcher.err.log"

function Get-Status {
    if (Test-Path -LiteralPath $pidFile) {
        $pidText = Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue
        if ($null -eq $pidText) {
            return @{ Status = "dead"; Pid = $null }
        }
        $pidText = $pidText.Trim()
        if ($pidText -notmatch '^\d+$') {
            return @{ Status = "dead"; Pid = $pidText }
        }
        try {
            Get-Process -Id ([int]$pidText) -ErrorAction Stop | Out-Null
            return @{ Status = "running"; Pid = [int]$pidText }
        } catch {
            return @{ Status = "dead"; Pid = [int]$pidText }
        }
    }
    return @{ Status = "stopped"; Pid = $null }
}

function Start-Service {
    $statusInfo = Get-Status
    if ($statusInfo.Status -eq "running") {
        Write-Host "服务已在运行中 (PID: $($statusInfo.Pid))"
        return $false
    }

    if ($statusInfo.Status -eq "dead") {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }

    $python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        $python = "python"
    }

    $startArgs = @{
        FilePath = $python
        ArgumentList = @("modian_tracker.py")
        WorkingDirectory = $PSScriptRoot
        WindowStyle = "Hidden"
        RedirectStandardOutput = $outLog
        RedirectStandardError = $errLog
        PassThru = $true
    }

    try {
        $proc = Start-Process @startArgs
    } catch {
        Write-Host "启动失败: $_"
        return $false
    }

    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $pidFile) {
            $pidText = Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue
            if ($null -ne $pidText) {
                $pidText = $pidText.Trim()
                if ($pidText -match '^\d+$') {
                    try {
                        Get-Process -Id ([int]$pidText) -ErrorAction Stop | Out-Null
                        Write-Host "已在后台启动抓取进程 (PID: $pidText)。"
                        Write-Host "抓取间隔与项目见: $(Join-Path $PSScriptRoot 'trackers.json')"
                        Write-Host "抓取日志: $(Join-Path $PSScriptRoot 'tracker_log.log')"
                        Write-Host "启动器日志: $outLog / $errLog"
                        Write-Host "PID 文件: $pidFile"
                        return $true
                    } catch {
                        # run.pid 可能刚写入但进程尚未就绪，继续等待
                    }
                }
            }
        }

        if ($proc.HasExited) {
            Write-Host "启动失败：Python 进程已退出 (ExitCode: $($proc.ExitCode))。"
            if (Test-Path -LiteralPath $errLog) {
                Write-Host "--- 启动器错误日志（末尾）---"
                Get-Content -LiteralPath $errLog -Tail 20 | ForEach-Object { Write-Host $_ }
            }
            return $false
        }

        Start-Sleep -Milliseconds 250
    }

    Write-Host "启动超时：未等到 run.pid。请检查 $errLog"
    return $false
}

function Show-State {
    $statusInfo = Get-Status
    if ($statusInfo.Status -eq "running") {
        Write-Host "服务运行中 (PID: $($statusInfo.Pid))"
    } elseif ($statusInfo.Status -eq "dead") {
        Write-Host "服务已死亡 (PID 文件存在但进程不存在: $($statusInfo.Pid))"
    } else {
        Write-Host "服务已停止"
    }
}

function Stop-Service {
    $statusInfo = Get-Status
    if ($statusInfo.Status -ne "running") {
        Write-Host "服务未在运行"
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        return
    }

    $processid = [int]$statusInfo.Pid
    Write-Host "正在停止服务 (PID: $processid)..."
    try {
        Stop-Process -Id $processid -Force -ErrorAction Stop

        $waited = 0
        while ($waited -lt 20) {
            try {
                Get-Process -Id $processid -ErrorAction Stop | Out-Null
                Start-Sleep -Seconds 1
                $waited++
            } catch {
                Write-Host "服务已停止"
                Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
                return
            }
        }

        Write-Host "强制杀死进程..."
        Get-Process -Id $processid -ErrorAction SilentlyContinue | Stop-Process -Force
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Write-Host "服务已强制停止"
    } catch {
        Write-Host "服务已停止"
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

function Restart-Service {
    Stop-Service
    Start-Sleep -Seconds 1
    Start-Service
}

switch ($Action) {
    "start" {
        Start-Service | Out-Null
    }
    "stop" {
        Stop-Service
    }
    "restart" {
        Restart-Service
    }
    { $_ -in "state", "status" } {
        Show-State
    }
    default {
        Write-Host "用法: .\$($MyInvocation.MyCommand.Name) [-Action {start|stop|restart|state|status}]"
        Write-Host "示例:"
        Write-Host "  .\run.ps1                       # 启动服务"
        Write-Host "  .\run.ps1 -Action start         # 启动服务"
        Write-Host "  .\run.ps1 -Action stop          # 停止服务"
        Write-Host "  .\run.ps1 -Action restart       # 重启服务"
        Write-Host "  .\run.ps1 -Action state         # 查看运行状态"
        exit 1
    }
}
