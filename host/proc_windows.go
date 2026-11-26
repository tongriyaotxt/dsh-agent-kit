//go:build windows

package main

import (
	"os/exec"
	"syscall"
)

// CREATE_NO_WINDOW：给子进程一个**存在但不可见**的控制台。
//
// 为什么必须这么做（2026-11-26 对照实验）：
//   宿主用 -H windowsgui 编译后没有控制台，于是 cmd /c dsh 里再 spawn 的 pwsh
//   工具进程会挂住 —— 同一个任务在「有控制台」的 Python 里 12.6 s 跑完，
//   在 GUI 宿主里 60 s 仍卡在 thinking，还留下 3 个 dsh-subprocess-local 残骸。
//   CREATE_NO_WINDOW 正好补上「有 console 但不弹窗」这个组合。
func prepareCmd(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: 0x08000000, // CREATE_NO_WINDOW
	}
}
