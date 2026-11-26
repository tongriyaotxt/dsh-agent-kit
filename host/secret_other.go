//go:build !windows

package main

import (
	"errors"
	"os"
	"path/filepath"
)

var errNoDPAPI = errors.New("非 Windows 平台没有 DPAPI")

// 非 Windows：不做加密（这里只支持 Windows 宿主）。
// 仍然返回错误，让调用方走「不保存、每次问」的路径，而不是假装安全。
func dpapiProtect(plain []byte) ([]byte, error) { return nil, errNoDPAPI }

func dpapiUnprotect(cipher []byte) ([]byte, error) { return nil, errNoDPAPI }

// 兜底：把权限收到 0600 再写，至少不是全局可读。
func writePrivate(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o600)
}
