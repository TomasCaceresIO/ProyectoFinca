"""
Formularios para la aplicación Gestión Ganadera.
"""
from django import forms
from django.core.exceptions import ValidationError
from .models import Explotacion, Finca, Animal, Parto, Ubicacion
from .services.animal_services import validar_crotal


class ExplotacionSetupForm(forms.ModelForm):
    """Formulario del Onboarding Wizard para crear la explotación inicial."""
    class Meta:
        model = Explotacion
        fields = ['nombre', 'codigo_rega']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej: Ganadería Las Encinas'
            }),
            'codigo_rega': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej: ES-190420001234'
            }),
        }


class AnimalForm(forms.ModelForm):
    """Formulario para crear/editar un animal."""
    class Meta:
        model = Animal
        fields = [
            'crotal', 'sexo', 'raza', 'fecha_nacimiento',
            'finca_actual', 'sub_ubicacion', 'madre'
        ]
        widgets = {
            'crotal': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '4 dígitos',
                'maxlength': '4',
                'pattern': '[0-9]{4}',
            }),
            'sexo': forms.Select(attrs={'class': 'form-control'}),
            'raza': forms.Select(attrs={'class': 'form-control'}),
            'fecha_nacimiento': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'finca_actual': forms.Select(attrs={'class': 'form-control'}),
            'sub_ubicacion': forms.Select(attrs={'class': 'form-control'}),
            'madre': forms.Select(attrs={'class': 'form-control'}),
        }

    def clean_crotal(self):
        crotal = self.cleaned_data.get('crotal')
        # Obtener pk de la instancia si estamos editando
        excluir_pk = self.instance.pk if self.instance.pk else None
        resultado = validar_crotal(crotal, excluir_pk=excluir_pk)
        
        if resultado['bloqueante']:
            raise ValidationError(resultado['mensaje'])
        
        # Guardar la alerta amarilla en el formulario para usarla en la vista
        self._crotal_alerta = resultado.get('alerta')
        self._crotal_mensaje = resultado.get('mensaje')
        return crotal


class AnimalBajaForm(forms.Form):
    """Formulario para dar de baja un animal."""
    motivo = forms.CharField(
        max_length=200,
        required=False,
        label='Motivo de baja',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: Venta, muerte natural, sacrificio...'
        })
    )
    confirmar = forms.BooleanField(
        required=True,
        label='Confirmo que deseo dar de baja este animal'
    )


class PartoForm(forms.ModelForm):
    """Formulario para registrar un parto."""
    # Checkbox para dar de alta la cría en el mismo formulario
    registrar_cria = forms.BooleanField(
        required=False,
        label='Dar de alta la cría ahora',
    )
    crotal_cria = forms.CharField(
        max_length=4,
        required=False,
        label='Crotal de la cría',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '4 dígitos',
            'maxlength': '4',
        })
    )
    sexo_cria = forms.ChoiceField(
        choices=[('', '---------'), ('M', 'Macho'), ('H', 'Hembra')],
        required=False,
        label='Sexo de la cría',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    raza_cria = forms.ChoiceField(
        choices=[('', '---------')] + Animal.RAZA_CHOICES,
        required=False,
        label='Raza de la cría',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    forzar_guardado = forms.BooleanField(
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = Parto
        fields = ['madre', 'fecha_parto', 'observaciones']
        widgets = {
            'madre': forms.Select(attrs={'class': 'form-control'}),
            'fecha_parto': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'observaciones': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
            }),
        }


class TrasladoForm(forms.Form):
    """Formulario para trasladar un animal a otra finca/ubicación."""
    finca_destino = forms.ModelChoiceField(
        queryset=Finca.objects.all(),
        label='Finca destino',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    ubicacion_destino = forms.ModelChoiceField(
        queryset=Ubicacion.objects.all(),
        label='Recinto destino',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    confirmar_reproductora = forms.BooleanField(
        required=False,
        label='Confirmo el traslado de esta reproductora al cebadero',
        widget=forms.HiddenInput(),
    )
