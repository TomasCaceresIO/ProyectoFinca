/**
 * Asistente de IA Ganadero - Entrada por Voz Nativa y Texto
 */

document.addEventListener('DOMContentLoaded', () => {
    const aiInput = document.getElementById('ai-input');
    const btnSubmit = document.getElementById('btn-ai-submit');
    const btnMic = document.getElementById('btn-audio-record');
    const modalContainer = document.getElementById('ai-modal-container');

    if (!aiInput || !btnSubmit || !btnMic || !modalContainer) {
        return;
    }

    // Función extractora estándar de cookies de Django
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // Obtener token CSRF desde cookie o input del DOM
    function getCsrfToken() {
        return getCookie('csrftoken') || document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    }

    // Cerrar modal
    window.closeAiModal = function() {
        modalContainer.innerHTML = '';
    };

    // Procesar envío a /asistente/preview/
    async function enviarComando(data) {
        btnSubmit.disabled = true;
        btnMic.disabled = true;
        aiInput.disabled = true;
        const originalText = btnSubmit.innerHTML;
        btnSubmit.innerHTML = '⏳ Analizando orden...';

        const token = getCsrfToken();
        if (data instanceof FormData && token && !data.has('csrfmiddlewaretoken')) {
            data.append('csrfmiddlewaretoken', token);
        }

        try {
            const headers = {};
            if (token) {
                headers['X-CSRFToken'] = token;
            }

            const response = await fetch('/asistente/preview/', {
                method: 'POST',
                headers: headers,
                body: data,
            });

            if (!response.ok) {
                throw new Error(`Error en el servidor: ${response.status}`);
            }

            const html = await response.text();
            modalContainer.innerHTML = html;

        } catch (error) {
            console.error('Error al procesar comando:', error);
            modalContainer.innerHTML = `
                <div id="ai-preview-modal" class="modal-overlay" style="display: flex;">
                    <div class="modal" style="max-width: 500px;">
                        <div class="modal-header d-flex justify-between align-center">
                            <h3 style="margin: 0; color: #ef4444;">Error del Asistente</h3>
                            <button type="button" class="btn btn-sm btn-outline" onclick="closeAiModal()">✕</button>
                        </div>
                        <div class="modal-body">
                            <div class="alert alert-danger">
                                No se pudo comunicar con el Asistente de IA. ${error.message}
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary w-100" onclick="closeAiModal()">Cerrar</button>
                        </div>
                    </div>
                </div>
            `;
        } finally {
            btnSubmit.disabled = false;
            btnMic.disabled = false;
            aiInput.disabled = false;
            btnSubmit.innerHTML = originalText;
        }
    }

    // Enviar texto del input
    function enviarTexto() {
        const texto = aiInput.value.trim();
        if (!texto) return;

        const formData = new FormData();
        formData.append('texto', texto);
        formData.append('csrfmiddlewaretoken', getCsrfToken());
        enviarComando(formData);
    }

    btnSubmit.addEventListener('click', enviarTexto);
    aiInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            enviarTexto();
        }
    });

    // --- VERIFICACIÓN DE CONTEXTO SEGURO (HTTPS / localhost) ---
    const esContextoSeguro = window.isSecureContext || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    const tieneSoporteVoz = Boolean(window.SpeechRecognition || window.webkitSpeechRecognition || (navigator.mediaDevices && navigator.mediaDevices.getUserMedia));

    if (!esContextoSeguro && !tieneSoporteVoz) {
        btnMic.style.opacity = '0.5';
        btnMic.title = 'La entrada por voz requiere conexión segura (HTTPS o localhost); utilice la entrada de texto';
        btnMic.addEventListener('click', (e) => {
            e.preventDefault();
            alert('La entrada por voz requiere conexión segura (HTTPS o localhost); utilice la entrada de texto.');
        });
        return;
    }

    // --- RECONOCIMIENTO DE VOZ NATIVO ---
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    let recognition = null;
    let isRecordingSpeech = false;

    // MediaRecorder Fallback
    let mediaRecorder = null;
    let audioChunks = [];
    let isRecordingMedia = false;

    if (SpeechRecognition && esContextoSeguro) {
        recognition = new SpeechRecognition();
        recognition.lang = 'es-ES';
        recognition.continuous = false;
        recognition.interimResults = false;

        recognition.onstart = () => {
            isRecordingSpeech = true;
            btnMic.classList.add('recording');
            btnMic.title = 'Escuchando... pulsa para parar';
            aiInput.placeholder = '🎙️ Escuchando... habla ahora (ej. "La 3014 parió hoy ternera 5012 limusina")';
        };

        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            aiInput.value = transcript;
            enviarTexto();
        };

        recognition.onerror = (event) => {
            console.warn('SpeechRecognition error:', event.error);
            stopSpeech();
        };

        recognition.onend = () => {
            stopSpeech();
        };

        function stopSpeech() {
            isRecordingSpeech = false;
            btnMic.classList.remove('recording');
            btnMic.title = 'Dictar por voz';
            aiInput.placeholder = "💬 Dicta o escribe un parto (ej. 'La 3014 parió hoy ternera 5012 limusina')...";
        }

        btnMic.addEventListener('click', () => {
            if (isRecordingSpeech) {
                recognition.stop();
            } else {
                try {
                    recognition.start();
                } catch (e) {
                    console.error('Error al iniciar reconocimiento:', e);
                }
            }
        });

    } else if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia && esContextoSeguro) {
        // Fallback a captura de audio nativa con MediaRecorder
        btnMic.addEventListener('click', async () => {
            if (isRecordingMedia && mediaRecorder) {
                mediaRecorder.stop();
                return;
            }

            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];

                mediaRecorder.ondataavailable = (e) => {
                    if (e.data.size > 0) {
                        audioChunks.push(e.data);
                    }
                };

                mediaRecorder.onstart = () => {
                    isRecordingMedia = true;
                    btnMic.classList.add('recording');
                    aiInput.placeholder = '🎙️ Grabando audio... pulsa de nuevo para procesar';
                };

                mediaRecorder.onstop = () => {
                    isRecordingMedia = false;
                    btnMic.classList.remove('recording');
                    aiInput.placeholder = "💬 Dicta o escribe un parto (ej. 'La 3014 parió hoy ternera 5012 limusina')...";
                    stream.getTracks().forEach(track => track.stop());

                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const formData = new FormData();
                    formData.append('audio', audioBlob, 'grabacion.webm');
                    formData.append('csrfmiddlewaretoken', getCsrfToken());
                    enviarComando(formData);
                };

                mediaRecorder.start();

            } catch (err) {
                console.error('No se pudo acceder al micrófono:', err);
                alert('No se pudo acceder al micrófono. Por favor, concede permisos en tu navegador.');
            }
        });
    } else {
        btnMic.style.opacity = '0.5';
        btnMic.title = 'La entrada por voz requiere conexión segura (HTTPS o localhost); utilice la entrada de texto';
        btnMic.addEventListener('click', (e) => {
            e.preventDefault();
            alert('La entrada por voz requiere conexión segura (HTTPS o localhost); utilice la entrada de texto.');
        });
    }
});
