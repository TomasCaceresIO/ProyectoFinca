"""
Formularios para la aplicación Gestión Ganadera.
"""
from django import forms
from django.core.exceptions import ValidationError
from .models import Explotacion, Finca, Animal, Parto, Ubicacion
from .services.animal_services import validar_crotal


class ExplotacionSetupForm(forms.Form):
    """Formulario del Onboarding Wizard para crear la explotación, finca inicial y recintos."""
    nombre = forms.CharField(
        max_length=200,
        label='Nombre de la Explotación',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: Ganadería Las Encinas'
        })
    )
    codigo_rega = forms.CharField(
        max_length=20,
        label='Código REGA',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: ES-190420001234'
        })
    )
    nombre_finca = forms.CharField(
        max_length=200,
        label='Nombre de la Finca Inicial',
        initial='Finca Principal',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: Finca Principal'
        })
    )
    cebadero = forms.BooleanField(
        required=False,
        initial=True,
        label='Crear recinto de Cebadero'
    )
    apartado = forms.BooleanField(
        required=False,
        initial=True,
        label='Crear recinto de Apartado'
    )


class AnimalForm(forms.ModelForm):
    """Formulario para crear/editar un animal."""
    class Meta:
        model = Animal
        fields = [
            'crotal', 'sexo', 'raza', 'fecha_nacimiento',
            'finca', 'sub_ubicacion', 'madre'
        ]
        widgets = {
            'crotal': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '4 dígitos',
                'maxlength': '4',
                'pattern': '[0-9]{4}',
            }),
            'sexo': forms.Select(attrs={'class': 'form-control'}),
            'raza': forms.Select(
                choices=[
                    ('RETINTA', 'Retinta'),
                    ('CHAROLESA', 'Charolesa'),
                    ('LIMUSINA', 'Limusina'),
                    ('FRISONA', 'Frisona'),
                    ('SIMMENTAL', 'Simmental'),
                    ('ANGUS', 'Angus'),
                    ('HEREFORD', 'Hereford'),
                    ('CRUZADO', 'Cruzado'),
                    ('OTRA', 'Otra'),
                ],
                attrs={'class': 'form-control'}
            ),
            'fecha_nacimiento': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'finca': forms.Select(attrs={'class': 'form-control'}),
            'sub_ubicacion': forms.Select(attrs={'class': 'form-control'}),
            'madre': forms.Select(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limitar madres solo a hembras
        self.fields['madre'].queryset = Animal.objects.filter(sexo='H')

    def clean_crotal(self):
        crotal = self.cleaned_data.get('crotal')
        excluir_pk = self.instance.pk if self.instance.pk else None
        resultado = validar_crotal(crotal, excluir_pk=excluir_pk)
        
        if resultado['bloqueante']:
            raise ValidationError(resultado['mensaje'])
        
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
    raza_cria = forms.CharField(
        required=False,
        initial='CRUZADO',
        label='Raza de la cría',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limitar campo madre solo a hembras VIVAS
        self.fields['madre'].queryset = Animal.objects.filter(sexo='H', estado_vital='VIVO')


class PartoEditForm(forms.ModelForm):
    """Formulario para editar la fecha u observaciones de un parto existente."""
    class Meta:
        model = Parto
        fields = ['fecha_parto', 'observaciones']
        widgets = {
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
    """Formulario para trasladar un animal a otra finca/sub_ubicación."""
    finca_destino = forms.ModelChoiceField(
        queryset=Finca.objects.all(),
        label='Finca destino',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    ubicacion_destino = forms.ChoiceField(
        choices=Animal.SUB_UBICACION_CHOICES,
        label='Recinto destino',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    confirmar_reproductora = forms.BooleanField(
        required=False,
        label='Confirmo el traslado de esta reproductora al cebadero',
        widget=forms.HiddenInput(),
    )
