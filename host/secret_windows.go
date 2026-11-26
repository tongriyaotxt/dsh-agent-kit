//go:build windows

package main

import (
	"fmt"
	"syscall"
	"unsafe"
)

var (
	crypt32    = syscall.NewLazyDLL("crypt32.dll")
	kernel32   = syscall.NewLazyDLL("kernel32.dll")
	procProtect   = crypt32.NewProc("CryptProtectData")
	procUnprotect = crypt32.NewProc("CryptUnprotectData")
	procLocalFree = kernel32.NewProc("LocalFree")
)

type dataBlob struct {
	cbData uint32
	pbData *byte
}

func newBlob(data []byte) *dataBlob {
	if len(data) == 0 {
		return &dataBlob{}
	}
	b := make([]byte, len(data))
	copy(b, data)
	return &dataBlob{cbData: uint32(len(b)), pbData: &b[0]}
}

func (b *dataBlob) bytes() []byte {
	if b.pbData == nil || b.cbData == 0 {
		return nil
	}
	out := make([]byte, b.cbData)
	copy(out, unsafe.Slice(b.pbData, b.cbData))
	return out
}

// dpapiProtect 用当前用户的 DPAPI 加密。
//
// 它挡的是「文件被拷走 / 被别的用户读」；**挡不住同用户的进程主动解密**。
// 但攻击成本从「read 一个文本文件」变成「写代码调 CryptUnprotectData」——
// 这是实质提升，不是绝对隔离（与 docs/10 的口径一致）。
func dpapiProtect(plain []byte) ([]byte, error) {
	in := newBlob(plain)
	var out dataBlob
	ret, _, err := procProtect.Call(
		uintptr(unsafe.Pointer(in)),
		0, 0, 0, 0,
		0, // 只给当前用户
		uintptr(unsafe.Pointer(&out)),
	)
	if ret == 0 {
		return nil, fmt.Errorf("CryptProtectData 失败: %v", err)
	}
	defer procLocalFree.Call(uintptr(unsafe.Pointer(out.pbData)))
	return out.bytes(), nil
}

func dpapiUnprotect(cipher []byte) ([]byte, error) {
	in := newBlob(cipher)
	var out dataBlob
	ret, _, err := procUnprotect.Call(
		uintptr(unsafe.Pointer(in)),
		0, 0, 0, 0,
		0,
		uintptr(unsafe.Pointer(&out)),
	)
	if ret == 0 {
		return nil, fmt.Errorf("CryptUnprotectData 失败: %v", err)
	}
	defer procLocalFree.Call(uintptr(unsafe.Pointer(out.pbData)))
	return out.bytes(), nil
}
