    .text
    .globl copy_probe
    .type copy_probe,@function
copy_probe:
    .cfi_startproc
    endbr64
#ifdef NARROW_CHECK
    test %edx,%edx
#else
    test %rdx,%rdx
#endif
    jz .Lempty
#ifdef CHANGED_LENGTH
    lea 1(%rdx),%rdx
#endif
    jmp memcpy@PLT
.Lempty:
    mov %rdi,%rax
    ret
    .cfi_endproc
    .size copy_probe,.-copy_probe
    .section .note.GNU-stack,"",@progbits
