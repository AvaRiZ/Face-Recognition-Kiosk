function getSwal() {
  return window.Swal || null;
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
