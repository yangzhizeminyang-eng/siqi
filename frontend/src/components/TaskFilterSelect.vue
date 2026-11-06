<script setup lang="ts">
import { Check, ChevronDown } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

export type TaskFilterOption = {
  value: string
  label: string
}

const props = withDefaults(defineProps<{
  modelValue: string
  options: TaskFilterOption[]
  ariaLabel: string
  disabled?: boolean
}>(), {
  disabled: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLDivElement | null>(null)
const open = ref(false)
const activeIndex = ref(0)
const hoveredIndex = ref<number | null>(null)
const selectedOption = computed(() => props.options.find((option) => option.value === props.modelValue) ?? props.options[0])

function syncActiveIndex() {
  const index = props.options.findIndex((option) => option.value === props.modelValue)
  activeIndex.value = index >= 0 ? index : 0
}

async function openMenu() {
  if (props.disabled || !props.options.length) return
  syncActiveIndex()
  hoveredIndex.value = null
  open.value = true
  await nextTick()
  menu.value?.focus()
}

function closeMenu(restoreFocus = false) {
  open.value = false
  hoveredIndex.value = null
  if (restoreFocus) trigger.value?.focus()
}

function toggleMenu() {
  if (open.value) closeMenu()
  else void openMenu()
}

function chooseOption(option: TaskFilterOption) {
  emit('update:modelValue', option.value)
  closeMenu(true)
}

function moveActive(step: number) {
  if (!props.options.length) return
  activeIndex.value = (activeIndex.value + step + props.options.length) % props.options.length
}

function onTriggerKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    if (open.value) moveActive(1)
    else void openMenu()
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (open.value) moveActive(-1)
    else void openMenu()
  } else if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    closeMenu()
  }
}

function onMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    moveActive(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(-1)
  } else if (event.key === 'Home') {
    event.preventDefault()
    activeIndex.value = 0
  } else if (event.key === 'End') {
    event.preventDefault()
    activeIndex.value = props.options.length - 1
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    const option = props.options[activeIndex.value]
    if (option) chooseOption(option)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    closeMenu(true)
  }
}

function onDocumentPointerdown(event: PointerEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) closeMenu()
}

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerdown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocumentPointerdown))
</script>

<template>
  <div ref="root" class="task-filter-select" :class="{ 'is-open': open, 'is-disabled': disabled }">
    <button
      ref="trigger"
      class="task-filter-select__trigger"
      type="button"
      :aria-label="ariaLabel"
      :aria-expanded="open"
      aria-haspopup="listbox"
      :disabled="disabled"
      @click="toggleMenu"
      @keydown="onTriggerKeydown"
    >
      <span class="task-filter-select__value">{{ selectedOption?.label }}</span>
      <ChevronDown class="task-filter-select__chevron" :size="15" aria-hidden="true" />
    </button>

    <Transition name="task-filter-select">
      <div
        v-if="open"
        ref="menu"
        class="task-filter-select__menu"
        role="listbox"
        tabindex="-1"
        :aria-label="ariaLabel"
        @keydown="onMenuKeydown"
        @mouseleave="hoveredIndex = null"
      >
        <button
          v-for="(option, index) in options"
          :key="option.value"
          class="task-filter-select__option"
          :class="{ 'is-active': index === activeIndex, 'is-hovered': index === hoveredIndex, 'is-selected': option.value === modelValue }"
          type="button"
          role="option"
          :aria-selected="option.value === modelValue"
          @mouseenter="activeIndex = index; hoveredIndex = index"
          @click="chooseOption(option)"
        >
          <span>{{ option.label }}</span>
          <Check v-if="option.value === modelValue" :size="15" aria-hidden="true" />
        </button>
      </div>
    </Transition>
  </div>
</template>
