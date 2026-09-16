
// Where the API lives. Empty in development, where the Vite proxy forwards /api to the local
// Flask server and everything is same-origin. In production the frontend is served by Vercel
// and the API by Railway, so a bare '/api' would resolve to Vercel and 404 — VITE_API_URL is
// what points it at the real origin. Trailing slashes are trimmed so the join stays exact.
const API_BASE = (import.meta.env?.VITE_API_URL || '').replace(/\/+$/, '')

export const apiUrl = (path) => `${API_BASE}/api${path}`

let refreshPromise = null

function refreshOnce(){
    if(!refreshPromise){
        refreshPromise = fetch(apiUrl('/auth/refresh'), { method: 'POST', credentials: 'include' })
        .finally(() => { refreshPromise = null })   
    }
    return refreshPromise
}


export async function apiFetch(path, options = {}, retry = true) {

    // FormData (file upload) must NOT get a JSON content-type — the browser sets
    // multipart with the correct boundary itself.
    const isFormData = options.body instanceof FormData
    const {
        headers: optionHeaders = {},
        timeoutMs,
        signal: optionSignal,
        ...fetchOptions
    } = options
    // Use one controller as a combined signal when the caller supplies both a manual
    // cancellation signal and a deadline. Previously, adding a caller signal silently
    // disabled timeoutMs.
    const timeoutController = timeoutMs ? new AbortController() : null
    let optionAbortHandler = null
    if (timeoutController && optionSignal) {
        optionAbortHandler = () => timeoutController.abort(optionSignal.reason)
        if (optionSignal.aborted) optionAbortHandler()
        else optionSignal.addEventListener('abort', optionAbortHandler, { once: true })
    }
    const timeoutId = timeoutController
        ? window.setTimeout(() => timeoutController.abort(), timeoutMs)
        : null

    let res
    try {
        res = await fetch(apiUrl(path), {
            ...fetchOptions,
            credentials: 'include',
            signal: timeoutController?.signal || optionSignal,
            headers: {
                ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
                ...optionHeaders,
            },
        })
    } catch (error) {
        if (timeoutController?.signal.aborted && error?.name === 'AbortError' && !optionSignal?.aborted) {
            const timeoutError = new Error('Request timed out. Please try again.')
            timeoutError.status = 408
            throw timeoutError
        }
        if (optionSignal?.aborted && error?.name === 'AbortError') {
            const canceledError = new Error('Extraction canceled.')
            canceledError.status = 499
            throw canceledError
        }
        throw error
    } finally {
        if (timeoutId !== null) window.clearTimeout(timeoutId)
        if (optionAbortHandler) optionSignal.removeEventListener('abort', optionAbortHandler)
    }
        
    if (res.status === 401 && retry && path !== '/auth/refresh') {
        
        const refreshed = await refreshOnce()
        if (refreshed.ok){return apiFetch(path, options, false)}
    
    } 

    const data = await res.json().catch(() => null)   

    if (!res.ok) {
        const error = new Error(data?.error || 'Request failed')
        error.status = res.status
        // machine-readable code when the server sends one, so a caller can recover rather
        // than only display the sentence
        error.reason = data?.reason || null
        throw error
    }

    return data
}

export function apiUpload(path, formData, onProgress, retry = true) {
    return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest()
        request.open('POST', apiUrl(path))
        request.withCredentials = true

        request.upload.addEventListener('progress', (event) => {
            if (!event.lengthComputable) return
            onProgress?.(Math.round((event.loaded / event.total) * 100))
        })

        request.addEventListener('load', async () => {
            if (request.status === 401 && retry && path !== '/auth/refresh') {
                try {
                    const refreshed = await refreshOnce()
                    if (refreshed.ok) {
                        resolve(apiUpload(path, formData, onProgress, false))
                        return
                    }
                } catch {
                    // Fall through and return the original authentication error.
                }
            }

            let data
            try {
                data = request.responseText ? JSON.parse(request.responseText) : null
            } catch {
                data = null
            }

            if (request.status < 200 || request.status >= 300) {
                const error = new Error(data?.error || 'Upload failed')
                error.status = request.status
                reject(error)
                return
            }

            resolve(data)
        })

        request.addEventListener('error', () => {
            const error = new Error('Upload failed. Check your connection and try again.')
            error.status = 0
            reject(error)
        })

        request.send(formData)
    })
}
