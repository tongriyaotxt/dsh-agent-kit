//go:build !windows

package main

import "os/exec"

// 非 Windows 平台不需要做任何事：没有 console 子系统的概念。
func prepareCmd(cmd *exec.Cmd) {}
