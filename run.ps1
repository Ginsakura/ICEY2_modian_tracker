<#
.SYNOPSIS
    在后台静默循环抓取摩点众筹数据
.DESCRIPTION
    配置启动参数后，以隐藏窗口的独立进程在后台运行 modian_tracker.py 的循环抓取：
      - 自动激活 .venv 虚拟环境，并优先使用其中的 uv
      - 以隐藏窗口启动，全程无界面（静默）
      - 抓取日志写入 modian_tracker.log；启动器输出写入 logs\ 目录
.PARAMETER Interval
    抓取间隔秒数，默认 60
.EXAMPLE
    .\run.ps1
    .\run.ps1 -Interval 30
#>
param(
    [int]$Interval = 60
)

$ErrorActionPreference = "Stop"

# 切换到脚本所在目录，确保相对路径（CSV/日志）正确
Set-Location -LiteralPath $PSScriptRoot

# 激活项目虚拟环境
$activate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path -LiteralPath $activate) {
    . $activate
} else {
    Write-Warning "未找到虚拟环境激活脚本: $activate"
}

# 优先使用虚拟环境内的 uv，否则回退到 PATH 中的 uv
$uv = Join-Path $PSScriptRoot ".venv\Scripts\uv.exe"
if (-not (Test-Path -LiteralPath $uv)) {
    $uv = "uv"
}

# 启动器输出日志目录
$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "launcher.out.log"
$errLog = Join-Path $logDir "launcher.err.log"

# 以隐藏窗口的独立进程在后台运行循环抓取
$proc = Start-Process -FilePath $uv `
    -ArgumentList @("run", "modian_tracker.py", "--interval", "$Interval") `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

Write-Host "已在后台启动抓取进程 (PID: $($proc.Id))，抓取间隔 $Interval 秒。"
Write-Host "抓取日志: $(Join-Path $PSScriptRoot 'modian_tracker.log')"
Write-Host "启动器日志: $outLog / $errLog"
Write-Host "停止命令: Stop-Process -Id $($proc.Id)"
