//go:build !windows

package main

// 非 Windows 平台没有控制台子系统的问题，日志走 stderr 就够。
func dbg(format string, args ...any) {}
