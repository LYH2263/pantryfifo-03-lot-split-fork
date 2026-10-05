import { api } from './api'

// 两段式在架分装:先预览(不落库),用户确认后再提交。成功返回 true。
export async function splitLot(x) {
  const q = Number(prompt(`从 #${x.id} ${x.name}(余量 ${x.qty_remain})拆出数量:`))
  if (!q) return false
  try {
    const p = await api('/split/preview', { method: 'POST', body: JSON.stringify({ lot_id: x.id, qty: q }) })
    if (!p.ok) { alert('不可分装:' + p.reason); return false }
    const msg = `母批 #${x.id} 余量 ${p.mother.qty_remain_before} → ${p.mother.qty_remain_after},子批 +${p.child.qty}。确认分装?`
    if (!confirm(msg)) return false
    await api('/split/confirm', { method: 'POST', body: JSON.stringify({ lot_id: x.id, qty: q }) })
    return true
  } catch (e) {
    alert('分装失败:' + e.message)
    return false
  }
}
