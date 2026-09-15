import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

function CheckIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.75">
      <path d="m3.5 8.25 2.75 2.75 6.25-6.25" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function ChevronIcon({ open }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      className={`h-3.5 w-3.5 shrink-0 text-muted transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="m4 6 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function SelectMenu({
  value,
  options,
  onChange,
  ariaLabel,
  disabled = false,
  className = '',
}) {
  const id = useId()
  const triggerRef = useRef(null)
  const menuRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [position, setPosition] = useState(null)
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value))
  const selectedOption = options[selectedIndex]

  const positionMenu = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return

    const rect = trigger.getBoundingClientRect()
    const width = Math.max(rect.width, 176)
    const estimatedHeight = Math.min(options.length * 44 + 12, 288)
    const spaceBelow = window.innerHeight - rect.bottom
    const opensAbove = spaceBelow < estimatedHeight + 12 && rect.top > spaceBelow
    const left = Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - width - 8))
    const top = opensAbove
      ? Math.max(8, rect.top - estimatedHeight - 6)
      : Math.min(window.innerHeight - 8, rect.bottom + 6)

    setPosition({ left, top, width, origin: opensAbove ? 'bottom' : 'top' })
  }, [options.length])

  function openMenu(index = selectedIndex) {
    if (disabled) return
    setActiveIndex(index)
    positionMenu()
    setOpen(true)
  }

  function closeMenu({ restoreFocus = false } = {}) {
    setOpen(false)
    if (restoreFocus) window.requestAnimationFrame(() => triggerRef.current?.focus())
  }

  function chooseOption(option, restoreFocus = false) {
    if (option.value !== value) onChange(option.value)
    closeMenu({ restoreFocus })
  }

  useEffect(() => {
    if (!open) return undefined

    positionMenu()
    window.requestAnimationFrame(() => menuRef.current?.focus())

    function handlePointerDown(event) {
      if (triggerRef.current?.contains(event.target) || menuRef.current?.contains(event.target)) return
      closeMenu()
    }

    function handleResize() {
      closeMenu()
    }

    function handleScroll(event) {
      if (menuRef.current?.contains(event.target)) return
      closeMenu()
    }

    document.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('resize', handleResize)
    window.addEventListener('scroll', handleScroll, true)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('resize', handleResize)
      window.removeEventListener('scroll', handleScroll, true)
    }
  }, [open, positionMenu])

  function handleTriggerKeyDown(event) {
    if (open && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault()
      chooseOption(options[activeIndex], true)
      return
    }
    if (open && event.key === 'Escape') {
      event.preventDefault()
      closeMenu({ restoreFocus: true })
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const offset = event.key === 'ArrowDown' ? 1 : -1
      if (open) {
        setActiveIndex((current) => (current + offset + options.length) % options.length)
      } else {
        openMenu((selectedIndex + offset + options.length) % options.length)
      }
    }
  }

  function handleMenuKeyDown(event) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeMenu({ restoreFocus: true })
      return
    }
    if (event.key === 'Tab') {
      closeMenu()
      return
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      setActiveIndex(event.key === 'Home' ? 0 : options.length - 1)
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const offset = event.key === 'ArrowDown' ? 1 : -1
      setActiveIndex((current) => (current + offset + options.length) % options.length)
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      chooseOption(options[activeIndex], true)
    }
  }

  return (
    <div className={`relative ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-controls={`${id}-listbox`}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={handleTriggerKeyDown}
        className={`control flex min-h-11 items-center justify-between gap-3 px-3 py-2.5 text-left text-sm ${open ? 'border-ink ring-1 ring-ink' : ''} disabled:opacity-45`}
      >
        <span className="truncate">{selectedOption?.label || 'Select'}</span>
        <ChevronIcon open={open} />
      </button>

      {open && position && createPortal(
        <div
          ref={menuRef}
          id={`${id}-listbox`}
          role="listbox"
          aria-label={ariaLabel}
          aria-activedescendant={`${id}-option-${activeIndex}`}
          tabIndex={-1}
          onKeyDown={handleMenuKeyDown}
          className="select-menu-panel fixed z-[70] max-h-72 overflow-y-auto rounded-md border border-border bg-pure-white p-1.5 outline-none"
          style={{ left: position.left, top: position.top, width: position.width, transformOrigin: position.origin }}
        >
          {options.map((option, index) => {
            const selected = option.value === value
            const active = index === activeIndex
            return (
              <button
                id={`${id}-option-${index}`}
                key={option.value}
                type="button"
                role="option"
                aria-selected={selected}
                onPointerMove={() => setActiveIndex(index)}
                onClick={() => chooseOption(option)}
                className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm ${
                  selected ? 'bg-obsidian text-white' : active ? 'bg-surface text-ink' : 'text-charcoal hover:bg-surface hover:text-ink'
                }`}
              >
                <span className="truncate">{option.label}</span>
                <span className={`grid h-4 w-4 shrink-0 place-items-center ${selected ? 'opacity-100' : 'opacity-0'}`}>
                  <CheckIcon />
                </span>
              </button>
            )
          })}
        </div>,
        document.body,
      )}
    </div>
  )
}
