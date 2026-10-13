<#
.SYNOPSIS
    在后台静默循环抓取摩点众筹数据，支持启动状态控制
.DESCRIPTION
    配置启动参数后，以隐藏窗口的独立进程在后台运行 modian_tracker.py 的循环抓取：
      - 自动激活 .venv 虚拟环境，并优先使用其中的 uv
      - 以隐藏窗口启动，全程无界面（静默）
      - 抓取日志写入 modian_tracker.log；启动器输出写入 logs\ 目录
      - PID 写入 run.pid 文件，支持 start/stop/restart/state 控制
.PARAMETER Action
    控制命令：start（启动）、stop（停止）、restart（重启）、state（查看状态），默认 start
.PARAMETER Interval
    抓取间隔秒数，默认 60
.EXAMPLE
    .\run.ps1
    .\run.ps1 -Interval 30
    .\run.ps1 -Action start -Interval 30
    .\run.ps1 -Action stop
    .\run.ps1 -Action restart
    .\run.ps1 -Action state
#>
param(
    [ValidateSet("start", "stop", "restart", "state", "status")]
    [string]$Action = "start",
    [int]$Interval = 60
)

# 设置控制台输出编码为 UTF-8 (65001)
[System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# 设置控制台输入编码为 UTF-8 (65001)
[System.Console]::InputEncoding = [System.Text.Encoding]::UTF8
# 设置管道传给外部程序的默认编码为 UTF-8 (65001)
$OutputEncoding = [System.Text.Encoding]::UTF8
# 修改 PowerShell 内部默认编码（影响 Out-File, Redirection 等）
$OutputEncoding = [System.Text.Encoding]::UTF8
# 设置 cmdlet 默认参数编码
$PSDefaultParameterValues['*:Encoding'] = 'utf8'

$ErrorActionPreference = "Stop"

# 切换到脚本所在目录，确保相对路径（CSV/日志）正确
Set-Location -LiteralPath $PSScriptRoot

# PID 文件路径
$pidFile = Join-Path $PSScriptRoot "run.pid"

# 激活项目虚拟环境
$activate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path -LiteralPath $activate) {
    . $activate
} else {
    Write-Warning "未找到虚拟环境激活脚本: $activate"
}

# 启动器输出日志目录
$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "launcher.out.log"
$errLog = Join-Path $logDir "launcher.err.log"

# 获取当前运行状态
function Get-Status {
    if (Test-Path -LiteralPath $pidFile) {
        $pid = Get-Content -LiteralPath $pidFile -Raw
        $pid = $pid.Trim()
        try {
            $proc = Get-Process -Id $pid -ErrorAction Stop
            return @{ Status = "running"; Pid = $pid }
        } catch {
            return @{ Status = "dead"; Pid = $pid }
        }
    } else {
        return @{ Status = "stopped"; Pid = $null }
    }
}

# 启动服务
function Start-Service {
    $statusInfo = Get-Status
    if ($statusInfo.Status -eq "running") {
        Write-Host "服务已在运行中 (PID: $($statusInfo.Pid))"
        return $false
    }
    
    # 清理死 PID 文件
    if ($statusInfo.Status -eq "dead") {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
    
    # 以隐藏窗口的独立进程在后台运行循环抓取
    $proc = Start-Process -FilePath "python" `
        -ArgumentList @("modian_tracker.py", "--interval", "$Interval") `
        -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru
    
    # 写入 PID 文件
    $proc.Id | Out-File -LiteralPath $pidFile -Encoding utf8 -Force
    
    Write-Host "已在后台启动抓取进程 (PID: $($proc.Id))，抓取间隔 $Interval 秒。"
    Write-Host "抓取日志: $(Join-Path $PSScriptRoot 'modian_tracker.log')"
    Write-Host "启动器日志: $outLog / $errLog"
    Write-Host "PID 文件: $pidFile"
    return $true
}

# 查看状态
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

# 停止服务
function Stop-Service {
    $statusInfo = Get-Status
    if ($statusInfo.Status -ne "running") {
        Write-Host "服务未在运行"
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        return
    }
    
    $pid = $statusInfo.Pid
    Write-Host "正在停止服务 (PID: $pid)..."
    
    try {
        $proc = Get-Process -Id $pid -ErrorAction Stop
        $proc | Stop-Process -Force
        
        # 等待进程退出
        $waited = 0
        while ($waited -lt 10) {
            try {
                $check = Get-Process -Id $pid -ErrorAction Stop
                Start-Sleep -Seconds 1
                $waited++
            } catch {
                Write-Host "服务已停止"
                Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
                return
            }
        }
        
        # 如果还在运行，强制杀死
        Write-Host "强制杀死进程..."
        Get-Process -Id $pid -ErrorAction SilentlyContinue | Stop-Process -Force
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Write-Host "服务已强制停止"
    } catch {
        Write-Host "服务已停止"
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

# 重启服务
function Restart-Service {
    Stop-Service
    Start-Sleep -Seconds 1
    Start-Service
}

# 主控制逻辑
switch ($Action) {
    "start" {
        Start-Service
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
        Write-Host "用法: .\$($MyInvocation.MyCommand.Name) [-Action {start|stop|restart|state|status}] [-Interval 间隔秒数]"
        Write-Host "示例:"
        Write-Host "  .\run.ps1                           # 启动服务（默认间隔 60 秒）"
        Write-Host "  .\run.ps1 -Action start -Interval 30 # 启动服务，间隔 30 秒"
        Write-Host "  .\run.ps1 -Action stop               # 停止服务"
        Write-Host "  .\run.ps1 -Action restart            # 重启服务"
        Write-Host "  .\run.ps1 -Action restart -Interval 30 # 重启服务，间隔 30 秒"
        Write-Host "  .\run.ps1 -Action state              # 查看运行状态"
        exit 1
    }
}
