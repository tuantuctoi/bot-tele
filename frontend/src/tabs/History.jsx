import { useState, useEffect } from 'react'
import { getHistory, getServices, buyNumber } from '../api'

const STATUS_LABELS = { 0: 'Đang chờ', 1: 'Hoàn thành', 2: 'Hết hạn' }
const STATUS_CLASS = { 0: 'badge-pending', 1: 'badge-success', 2: 'badge-expired' }

const today = () => new Date().toISOString().slice(0, 10)

export default function History() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState({ status: '', limit: '20', fromDate: today(), toDate: today() })
  const [rebuying, setRebuying] = useState(null)
  const [rebuyMsg, setRebuyMsg] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    setRebuyMsg('')
    try {
      const data = await getHistory(filter)
      if (data.success) setItems(data.data || [])
      else setError(data.message)
    } catch { setError('Lỗi kết nối') }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const handleRebuy = async (item) => {
    if (!item.PhoneOriginal) return
    setRebuying(item.ID)
    setRebuyMsg('')
    try {
      const data = await buyNumber({ serviceId: item.ServiceID, number: item.PhoneOriginal })
      if (data.success) {
        setRebuyMsg(`✅ Thuê lại thành công! SĐT: 0${data.data.phone_number} | Request: ${data.data.request_id}`)
      } else {
        setRebuyMsg(`❌ ${data.message}`)
      }
    } catch { setRebuyMsg('❌ Lỗi kết nối') }
    setRebuying(null)
  }

  const copyText = (t) => navigator.clipboard?.writeText(t)

  return (
    <div>
      <div className="section-title">Lịch sử thuê số</div>

      {/* Filters */}
      <div className="card">
        <div className="form-group">
          <label className="form-label">Trạng thái</label>
          <select className="form-select" value={filter.status}
            onChange={e => setFilter(f => ({ ...f, status: e.target.value }))}>
            <option value="">Tất cả</option>
            <option value="1">Hoàn thành</option>
            <option value="0">Đang chờ</option>
            <option value="2">Hết hạn</option>
          </select>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label">Từ ngày</label>
            <input type="date" className="form-input" value={filter.fromDate}
              onChange={e => setFilter(f => ({ ...f, fromDate: e.target.value }))} />
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label">Đến ngày</label>
            <input type="date" className="form-input" value={filter.toDate}
              onChange={e => setFilter(f => ({ ...f, toDate: e.target.value }))} />
          </div>
        </div>
        <div className="form-group">
          <label className="form-label">Số lượng</label>
          <select className="form-select" value={filter.limit}
            onChange={e => setFilter(f => ({ ...f, limit: e.target.value }))}>
            <option value="20">20</option>
            <option value="50">50</option>
            <option value="100">100</option>
          </select>
        </div>
        <button className="btn" onClick={load} disabled={loading}>
          {loading ? <><span className="spinner" />Đang tải...</> : '🔍 Tìm kiếm'}
        </button>
      </div>

      {error && <div className="error-msg">{error}</div>}
      {rebuyMsg && (
        <div className={`card`} style={{ borderLeft: `3px solid ${rebuyMsg.startsWith('✅') ? '#30d158' : '#ff453a'}` }}>
          {rebuyMsg}
        </div>
      )}

      {items.length === 0 && !loading && <div className="empty-state">Không có dữ liệu</div>}

      {items.map(item => (
        <div key={item.ID} className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
            <div>
              <div style={{ fontWeight: 600 }}>{item.ServiceName}</div>
              <div style={{ fontSize: 13, color: 'var(--tg-hint)', marginTop: 2 }}>
                SĐT: {item.PhoneOriginal || ('0' + item.Phone)}
                <button className="copy-btn" onClick={() => copyText(item.PhoneOriginal || '0' + item.Phone)}>Copy</button>
              </div>
            </div>
            <span className={`badge ${STATUS_CLASS[item.Status]}`}>{STATUS_LABELS[item.Status]}</span>
          </div>

          {item.Status === 1 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 13, color: 'var(--tg-hint)' }}>Mã OTP</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--tg-btn)', letterSpacing: 2 }}>
                {item.Code}
                <button className="copy-btn" onClick={() => copyText(item.Code)}>Copy</button>
              </div>
              {item.IsSound === true || item.IsSound === 'true' ? (
                <audio controls src={item.SmsContent} />
              ) : (
                <div style={{ fontSize: 12, color: 'var(--tg-hint)', marginTop: 4, lineHeight: 1.4 }}>{item.SmsContent}</div>
              )}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: 'var(--tg-hint)' }}>
            <span>{item.Price?.toLocaleString('vi-VN')}đ · {item.CreatedTime?.slice(0, 16).replace('T', ' ')}</span>
            {item.PhoneOriginal && (
              <button className="btn secondary"
                style={{ width: 'auto', margin: 0, padding: '6px 12px', fontSize: 12 }}
                onClick={() => handleRebuy(item)}
                disabled={rebuying === item.ID}>
                {rebuying === item.ID ? '...' : '↩ Thuê lại'}
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
