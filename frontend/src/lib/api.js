export async function apiFetch(path, options = {}) {
    
    const res = await fetch('/api' + path, {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        ...options,
    })
    
    const data = await res.json().catch(() => null)   
    
    if (!res.ok) {                      
        throw new Error(data?.error || 'Request failed')
    }
    
    return data
}
