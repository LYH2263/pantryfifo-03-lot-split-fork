<template>
  <span class="repack">
    <button type="button" class="linkbtn" @click="start">分装</button>
    <span v-if="active" class="repack-form">
      <input
        type="number" v-model.number="qty" min="0" :max="lot.qty_remain" step="any"
        placeholder="拆出量" @keyup.enter="confirm"
      />
      <button type="button" @click="confirm">确认分装</button>
      <button type="button" class="ghost" @click="cancel">取消</button>
      <span class="preview">
        预览（不改余量）：母批 {{ motherAfter }} + 子批 {{ childQty }} = {{ total }}
        <template v-if="!valid"> · 拆出量须大于 0 且小于余量 {{ lot.qty_remain }}</template>
      </span>
      <span v-if="err" class="err">{{ err }}</span>
    </span>
  </span>
</template>
<script setup>
import { ref, computed } from 'vue'
import { api } from '../api'

const props = defineProps({ lot: { type: Object, required: true } })
const emit = defineEmits(['done'])

const active = ref(false)
const qty = ref(0)
const err = ref('')

const childQty = computed(() => {
  const n = Number(qty.value)
  return Number.isFinite(n) && n > 0 ? n : 0
})
const motherAfter = computed(() => {
  const n = Number(props.lot.qty_remain) - childQty.value
  return Math.round(n * 1000) / 1000
})
const total = computed(() => Math.round((motherAfter.value + childQty.value) * 1000) / 1000)
const valid = computed(() => childQty.value > 0 && motherAfter.value > 0)

function start() { active.value = true; qty.value = 0; err.value = '' }
function cancel() { active.value = false; err.value = '' }

async function confirm() {
  if (!valid.value) { err.value = '拆出量须大于 0 且小于余量'; return }
  err.value = ''
  try {
    const res = await api('/split', {
      method: 'POST',
      body: JSON.stringify({ lot_id: props.lot.id, qty: childQty.value }),
    })
    active.value = false
    emit('done', res)
  } catch (e) {
    // Whole order failed server-side; nothing changed, keep the form open.
    err.value = '分装失败：' + e.message
  }
}
</script>
