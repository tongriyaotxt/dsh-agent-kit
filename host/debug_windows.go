//go:build windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

var debugOn = os.Getenv("DSHAPPS_DEBUG") != ""

// dbg 把宿主内部的关键步骤写到 %TEMP%\dshapps.log。
// 只在 DSHAPPS_DEBUG=1 时写 —— GUI 宿主没有控制台，出问题只能靠文件。
func dbg(format string, args ...any) {
	if !debugOn {
		return
	}
	path := filepath.Join(os.TempDir(), "dshapps.log")
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	fmt.Fprintf(f, "%s  %s\n", time.Now().Format("15:04:05.000"),
		fmt.Sprintf(format, args...))
}
