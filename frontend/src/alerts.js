function getSwal() {
  return window.Swal || null;
}

const SWAL_Z_INDEX = 3000;

function applySwalZIndex(popup) {
  const container = popup?.closest?.('.swal2-container') || document.querySelector('.swal2-container');
  if (container) {
    container.style.zIndex = String(SWAL_Z_INDEX);
  }
}

export async function showAlert({
  icon = 'info',
  title,
  text,
  timer,
  showConfirmButton = true
}) {
  const swal = getSwal();
  if (swal) {
    await swal.fire({
      icon,
      title,
      text,
      zIndex: SWAL_Z_INDEX,
      didOpen: applySwalZIndex,
      timer,
      timerProgressBar: Boolean(timer),
      showConfirmButton
    });
    return;
  }

  const message = [title, text].filter(Boolean).join(': ');
  window.alert(message);
}

export async function showSuccess(title, text) {
  await showAlert({ icon: 'success', title, text });
}

export async function showError(title, text) {
  await showAlert({ icon: 'error', title, text });
}

export async function confirmAction({
  title,
  text,
  confirmButtonText = 'Continue',
  confirmButtonColor = '#0d6efd',
  icon = 'warning'
}) {
  const swal = getSwal();
  if (swal) {
    const result = await swal.fire({
      icon,
      title,
      text,
      zIndex: SWAL_Z_INDEX,
      didOpen: applySwalZIndex,
      showCancelButton: true,
      confirmButtonText,
      cancelButtonText: 'Cancel',
      confirmButtonColor,
      cancelButtonColor: '#6c757d',
      reverseButtons: true
    });
    return result.isConfirmed;
  }

  return window.confirm(text);
}

export function getErrorMessage(error, fallback = 'Unexpected error occurred.') {
  return error?.data?.message || error?.message || fallback;
}
